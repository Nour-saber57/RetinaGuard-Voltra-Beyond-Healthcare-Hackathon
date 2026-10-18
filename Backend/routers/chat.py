import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException
from google import genai
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from chat_prompts import (
    SUMMARY_SELECTION_SCHEMA,
    SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION_VERSION,
    TREND_EXPLANATION_SCHEMA,
)
from database import get_db
from models import Appointment, Case, Clinic, HealthMeasurement, Patient
from schemas import PatientChatRequest, PatientChatResponse, PatientChartContext
from security import require_patient

router = APIRouter(prefix="/chat", tags=["Patient chat"])
logger = logging.getLogger(__name__)
CAIRO = ZoneInfo("Africa/Cairo")
MEASUREMENT_LIMIT = 10
SCREENING_LIMIT = 10
APPOINTMENT_LIMIT = 10
SUMMARY_LIMIT = 10
MAX_REPLY_LENGTH = 1200
PROVIDER_TIMEOUT_SECONDS = 12.0
TREND_READING_LIMIT = 1000

URGENT_PATTERNS = (
    re.compile(r"\b(?:sudden(?:ly)?\s+(?:vision|sight)\s+loss|loss of (?:vision|sight)(?:\s+suddenly)?)\b", re.I),
    re.compile(r"\bnew flashes?\b", re.I),
    re.compile(r"\b(?:many new|sudden increase in|suddenly more)\s+floaters?\b", re.I),
    re.compile(r"\bcurtain(?:[- ]like)?\s+(?:over|across)\s+(?:my\s+)?(?:vision|sight)\b|\bshadow\s+(?:over|across)\s+(?:my\s+)?(?:vision|sight)\b", re.I),
    re.compile(r"(?:فقدان|فقدت|فقدانٌ).{0,20}(?:مفاجئ|فجأة).{0,12}(?:النظر|الرؤية|للنظر|للرؤية)|(?:النظر|الرؤية|للنظر|للرؤية).{0,20}(?:فجأة|مفاجئ)", re.I),
    re.compile(r"(?:ومضات|وميض)[^.!?؟،؛\n]{0,18}(?:جديدة|جديد)|(?:جديدة|جديد)[^.!?؟،؛\n]{0,18}(?:ومضات|وميض)", re.I),
    re.compile(r"(?:زيادة مفاجئة|عدد كبير من العوائم|عوائم جديدة كثيرة)|(?:ستارة|ظل).{0,20}(?:الرؤية|النظر)", re.I),
)
ENGLISH_NEGATION = re.compile(r"\b(?:no|not|never|without|don't|do not|doesn't|didn't|haven't|hasn't|isn't|aren't)\b", re.I)
ARABIC_NEGATION = re.compile(r"(?:لا يوجد|لا توجد|لا|ليس|ليست|لم|لن|بدون|ما عندي|ما أعاني|لا أعاني)")
ENGLISH_HYPOTHETICAL = re.compile(r"\b(?:what if|if|suppose|hypothetically|imagine|do i have|am i having|could i have|could this be|is it possible)\b", re.I)
ARABIC_HYPOTHETICAL = re.compile(r"(?:ماذا لو|لو افترضنا|افترض|إذا حدث|اذا حدث|هل أعاني|هل اعاني|هل لدي|هل عندي|ممكن يكون)")
ENGLISH_DEFINITION = re.compile(
    r"\b(?:define|meaning of|explain)\b|\bwhat does .+? mean\b|\bwhat is (?:an? )?(?:hba1c|a1c|weight|blood pressure)\b",
    re.I,
)
ARABIC_DEFINITION = re.compile(r"(?:ما معنى|ماذا يعني|اشرح معنى|ما المقصود)")


def _coverage(total_count: int, included_count: int) -> dict:
    return {"total_count": total_count, "included_count": included_count, "truncated": total_count > included_count}


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _measurement_record(item: HealthMeasurement) -> dict:
    record = {
        "id": f"measurement:{item.id}",
        "source": "health_measurements",
        "kind": item.kind,
        "date": item.measured_on.isoformat(),
    }
    if item.kind == "bp":
        record.update({"systolic": item.value, "diastolic": item.diastolic, "unit": "mmHg"})
    else:
        record.update({"value": item.value, "unit": "kg" if item.kind == "weight" else "%"})
    return record


def _appointment_record(appointment: Appointment, clinic_name: str) -> dict:
    return {
        "id": f"appointment:{appointment.reference}",
        "source": "appointments",
        "reference": appointment.reference,
        "clinic": clinic_name,
        "date": appointment.appointment_date.isoformat(),
        "time": appointment.appointment_time.strftime("%H:%M"),
        "timezone": "Africa/Cairo",
        "status": appointment.status,
        "booking_type": "demo",
        "clinic_contacted": False,
        "email_delivery_status": "not_persisted",
    }


def patient_record_context(patient: Patient, db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(CAIRO)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CAIRO)
    else:
        now = now.astimezone(CAIRO)

    measurements = {}
    measurement_coverage = {}
    for kind in ("weight", "a1c", "bp"):
        total_count = db.scalar(select(func.count()).select_from(HealthMeasurement).where(
            HealthMeasurement.patient_id == patient.id,
            HealthMeasurement.kind == kind,
        )) or 0
        rows = db.scalars(select(HealthMeasurement).where(
            HealthMeasurement.patient_id == patient.id,
            HealthMeasurement.kind == kind,
        ).order_by(desc(HealthMeasurement.measured_on), desc(HealthMeasurement.id)).limit(MEASUREMENT_LIMIT)).all()
        measurements[kind] = [_measurement_record(item) for item in rows]
        measurement_coverage[kind] = _coverage(total_count, len(rows))

    screening_count = db.scalar(select(func.count()).select_from(Case).where(Case.patient_id == patient.id)) or 0
    screening_rows = db.scalars(select(Case).where(
        Case.patient_id == patient.id,
    ).order_by(desc(Case.created_at), desc(Case.id)).limit(SCREENING_LIMIT)).all()
    screenings = [{
        "id": f"screening:{item.id}",
        "source": "cases",
        "date": _utc_iso(item.created_at),
        "status": item.status,
        "prediction": item.prediction[:500] if item.prediction else None,
        "prediction_truncated": bool(item.prediction and len(item.prediction) > 500),
        "image_analysis_available": False,
    } for item in screening_rows]

    appointment_count = db.scalar(select(func.count()).select_from(Appointment).where(
        Appointment.patient_id == patient.id,
    )) or 0
    appointment_rows = db.execute(select(Appointment, Clinic.name).join(
        Clinic, Clinic.id == Appointment.clinic_id,
    ).where(Appointment.patient_id == patient.id).order_by(
        desc(Appointment.appointment_date), desc(Appointment.appointment_time),
    ).limit(APPOINTMENT_LIMIT)).all()
    appointments = [_appointment_record(appointment, name) for appointment, name in appointment_rows]

    clock_time = now.time().replace(tzinfo=None)
    upcoming_row = db.execute(select(Appointment, Clinic.name).join(
        Clinic, Clinic.id == Appointment.clinic_id,
    ).where(
        Appointment.patient_id == patient.id,
        Appointment.status == "Confirmed",
        or_(
            Appointment.appointment_date > now.date(),
            and_(Appointment.appointment_date == now.date(), Appointment.appointment_time > clock_time),
        ),
    ).order_by(Appointment.appointment_date, Appointment.appointment_time).limit(1)).first()

    return {
        "metadata": {
            "schema_version": "1.0",
            "system_instruction_version": SYSTEM_INSTRUCTION_VERSION,
            "generated_at": now.isoformat(),
            "timezone": "Africa/Cairo",
            "app_mode": "demo",
            "capabilities": {
                "read_only_record_lookup": True,
                "evidence_id_summary_selection": True,
                "write_actions": False,
                "image_analysis": False,
                "live_clinic_booking": False,
                "reviewed_education": False,
            },
            "units": {"weight": "kg", "a1c": "%", "blood_pressure": "systolic/diastolic mmHg"},
        },
        "coverage": {
            "measurements": measurement_coverage,
            "screenings": _coverage(screening_count, len(screenings)),
            "appointments": _coverage(appointment_count, len(appointments)),
        },
        "profile": {
            "diabetes_type": {"id": "profile:diabetes_type", "source": "patients.diabetes_type", "value": patient.diabetes_type},
            "care_progress": {
                "id": "profile:care_progress",
                "source": "patients.care_progress_fields",
                "awareness_completed_flag": patient.awareness_completed,
                "results_appointment_reference": patient.results_appointment,
                "followup_appointment_reference": patient.followup_appointment,
            },
        },
        "measurements": measurements,
        "screenings": screenings,
        "appointments": appointments,
        "next_upcoming_confirmed_appointment": (
            _appointment_record(*upcoming_row) if upcoming_row else None
        ),
    }


def generate_gemini_reply(prompt: str, response_schema: dict | None = None) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if not api_key or not model:
        raise HTTPException(status_code=503, detail="Gemini chat is not configured")

    client = None
    try:
        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=model,
            system_instruction=SYSTEM_INSTRUCTION,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema or SUMMARY_SELECTION_SCHEMA,
            },
            store=False,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            generation_config={"max_output_tokens": 256, "thinking_level": "low"},
        )
        reply = interaction.output_text
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Gemini returned an empty response")
        return reply.strip()
    except (TimeoutError, httpx.TimeoutException) as error:
        logger.warning("Gemini chat request timed out")
        raise HTTPException(status_code=504, detail="The assistant timed out. Please try again.") from error
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Gemini chat request failed (%s)", type(error).__name__)
        raise HTTPException(status_code=502, detail="The assistant could not respond. Please try again.") from error
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _language(message: str) -> str:
    return "ar" if re.search(r"[\u0600-\u06ff]", message) else "en"


def _quoted_text_removed(message: str) -> str:
    return re.sub(r'"[^"\n]*"|\'[^\'\n]*\'|“[^”]*”|‘[^’]*’|«[^»]*»', " ", message)


def urgent_symptom_status(message: str) -> str | None:
    text = _quoted_text_removed(message)
    hypothetical_found = False
    for pattern in URGENT_PATTERNS:
        for match in pattern.finditer(text):
            sentence_start = max(text.rfind(mark, 0, match.start()) for mark in (".", ",", "!", "?", "؟", "،", "؛", "\n"))
            prefix = text[sentence_start + 1 : match.start()]
            contrast = re.search(r"\b(?:but|however|yet|except)\b|(?:لكن|ولكن|بل)", prefix, re.I)
            if contrast:
                prefix = prefix[contrast.end() :]
            if ENGLISH_NEGATION.search(prefix) or ARABIC_NEGATION.search(prefix):
                continue
            if ENGLISH_HYPOTHETICAL.search(prefix) or ARABIC_HYPOTHETICAL.search(prefix):
                hypothetical_found = True
                continue
            if (ENGLISH_DEFINITION.search(prefix) or ARABIC_DEFINITION.search(prefix)) and not re.search(
                r"\b(?:I|I've|I'm|my|currently|experiencing|having)\b|(?:عندي|أعاني|أشعر|أرى)", prefix, re.I
            ):
                continue
            return "current"
    return "hypothetical" if hypothetical_found else None


def _urgent_reply(message: str, status: str) -> str:
    if _language(message) == "ar":
        if status == "hypothetical":
            return "إذا حدث فقدان مفاجئ للرؤية أو ومضات جديدة أو زيادة مفاجئة في العوائم أو ستار/ظل على الرؤية، فاطلب رعاية عيون أو طوارئ فورًا. إذا كانت الأعراض تحدث الآن، فلا تنتظر موعدًا روتينيًا."
        return "فقدان الرؤية المفاجئ أو الومضات الجديدة أو الزيادة المفاجئة في العوائم أو وجود ستار/ظل على الرؤية يحتاج إلى رعاية عيون أو طوارئ فورًا. لا تنتظر موعدًا روتينيًا."
    if status == "hypothetical":
        return "If sudden vision loss, new flashes, a sudden increase in floaters, or a curtain/shadow over vision occurs, seek immediate eye or emergency care. If this is happening now, do not wait for a routine appointment."
    return "Sudden vision loss, new flashes, a sudden increase in floaters, or a curtain/shadow over vision needs immediate eye or emergency care. Do not wait for a routine appointment."


def _is_medication_dose_request(message: str) -> bool:
    medication = re.search(r"\b(?:insulin|medication|medicine|drug|dose|dosage)\b|(?:الأنسولين|انسولين|دواء|جرعة)", message, re.I)
    action = re.search(r"\b(?:change|adjust|increase|decrease|skip|take|should|can i|how much)\b|(?:تغيير|أعدل|زيادة|خفض|آخذ|كم|هل أ)", message, re.I)
    return bool(medication and action)


def _asks_about_another_patient(message: str) -> bool:
    return bool(re.search(
        r"\b(?:another|other|someone else's|somebody else's)\s+(?:patient|person|user|account)\b|"
        r"\b(?:patient|person|user)\s+\d+\b|"
        r"(?:مريض آخر|مريضه الآخر|بيانات مريض|معلومات مريض آخر|شخص آخر|حساب شخص آخر)",
        message,
        re.I,
    ))


def _measurement_kind(message: str) -> str | None:
    if re.search(r"\b(?:hba1c|a1c|glycated hemoglobin)\b|(?:السكر التراكمي|التراكمي)", message, re.I):
        return "a1c"
    if re.search(r"\b(?:blood pressure|bp|systolic|diastolic)\b|(?:ضغط الدم|الضغط)", message, re.I):
        return "bp"
    if re.search(r"\bweight\b|(?:الوزن)", message, re.I):
        return "weight"
    return None


def _mentioned_measurement_kinds(message: str) -> set[str]:
    kinds = set()
    if re.search(r"\b(?:hba1c|a1c|glycated hemoglobin)\b|(?:السكر التراكمي|التراكمي)", message, re.I):
        kinds.add("a1c")
    if re.search(r"\b(?:blood pressure|bp|systolic|diastolic)\b|(?:ضغط الدم|الضغط)", message, re.I):
        kinds.add("bp")
    if re.search(r"\bweight\b|(?:الوزن)", message, re.I):
        kinds.add("weight")
    return kinds


def _is_graph_insight_question(message: str) -> bool:
    return bool(re.search(
        r"\b(?:graph|chart|trend|progress)\b|"
        r"\bhow am i (?:doing|progressing)\b|\bwhat do you think about my (?:numbers|measurements)\b|"
        r"(?:الرسم البياني|الرسم|المنحنى|القياسات|القراءات|كيف أتقدم|كيف وضعي)",
        message,
        re.I,
    ))


def _chart_metric_for_question(
    message: str,
    chart_context: PatientChartContext | None,
) -> tuple[str | None, str | None]:
    explicit = _mentioned_measurement_kinds(message)
    if len(explicit) > 1:
        return None, "Which graph should I look at: weight, HbA1c, or blood pressure?"
    if explicit:
        return next(iter(explicit)), None
    if chart_context and chart_context.metric:
        return chart_context.metric, None
    visible = chart_context.visible_metrics if chart_context else []
    if len(visible) == 1:
        return visible[0], None
    return None, "Which graph should I look at: weight, HbA1c, or blood pressure?"


def measurement_insights(
    patient: Patient,
    db: Session,
    metric: str,
    date_range: str,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(CAIRO)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CAIRO)
    else:
        now = now.astimezone(CAIRO)

    filters = [
        HealthMeasurement.patient_id == patient.id,
        HealthMeasurement.kind == metric,
    ]
    range_days = None if date_range == "all" else int(date_range)
    window_start = now.date() - timedelta(days=range_days) if range_days is not None else None
    if window_start is not None:
        filters.append(HealthMeasurement.measured_on >= window_start)

    base = select(HealthMeasurement).where(*filters)
    total_in_range = db.scalar(select(func.count()).select_from(HealthMeasurement).where(*filters)) or 0
    count_all_time = db.scalar(select(func.count()).select_from(HealthMeasurement).where(
        HealthMeasurement.patient_id == patient.id,
        HealthMeasurement.kind == metric,
    )) or 0
    recent_rows = db.scalars(
        base.order_by(desc(HealthMeasurement.measured_on), desc(HealthMeasurement.id))
        .limit(TREND_READING_LIMIT)
    ).all()
    rows_ascending = list(reversed(recent_rows))
    truncated = total_in_range > len(recent_rows)
    first_date = db.scalar(select(func.min(HealthMeasurement.measured_on)).where(*filters))
    last_date = db.scalar(select(func.max(HealthMeasurement.measured_on)).where(*filters))

    def record(item: HealthMeasurement | None) -> dict | None:
        return _measurement_record(item) if item is not None else None

    earliest = db.scalar(base.order_by(HealthMeasurement.measured_on, HealthMeasurement.id).limit(1))
    latest = recent_rows[0] if recent_rows else None
    previous = recent_rows[1] if len(recent_rows) > 1 else None

    def extreme(ordering, non_null_diastolic: bool = False) -> dict | None:
        statement = base.where(HealthMeasurement.diastolic.is_not(None)) if non_null_diastolic else base
        item = db.scalar(statement.order_by(ordering, HealthMeasurement.measured_on, HealthMeasurement.id).limit(1))
        return record(item)

    if metric == "bp":
        minimum = {
            "systolic": extreme(HealthMeasurement.value.asc()),
            "diastolic": extreme(HealthMeasurement.diastolic.asc(), non_null_diastolic=True),
        }
        maximum = {
            "systolic": extreme(HealthMeasurement.value.desc()),
            "diastolic": extreme(HealthMeasurement.diastolic.desc(), non_null_diastolic=True),
        }
        change = None
        if latest and previous:
            change = {
                "systolic": latest.value - previous.value,
                "diastolic": latest.diastolic - previous.diastolic
                if latest.diastolic is not None and previous.diastolic is not None else None,
            }
    else:
        minimum = extreme(HealthMeasurement.value.asc())
        maximum = extreme(HealthMeasurement.value.desc())
        change = round(latest.value - previous.value, 6) if latest and previous else None

    gaps = [
        (rows_ascending[index].measured_on - rows_ascending[index - 1].measured_on).days
        for index in range(1, len(rows_ascending))
    ]
    stale_days = (now.date() - latest.measured_on).days if latest and latest.measured_on <= now.date() else None
    units = {"weight": "kg", "a1c": "%", "bp": "mmHg"}
    return {
        "metric": metric,
        "unit": units[metric],
        "date_range": date_range,
        "range_start": window_start.isoformat() if window_start else (first_date.isoformat() if first_date else None),
        "range_end": now.date().isoformat(),
        "timezone": "Africa/Cairo",
        "current_date": now.date().isoformat(),
        "measurement_count": total_in_range,
        "account_measurement_count": count_all_time,
        "included_count": len(recent_rows),
        "complete": not truncated,
        "truncated": truncated,
        "dates_covered": {"first": first_date.isoformat() if first_date else None,
                          "last": last_date.isoformat() if last_date else None},
        "latest": record(latest),
        "previous": record(previous),
        "earliest": record(earliest),
        "minimum": minimum,
        "maximum": maximum,
        "absolute_change": change,
        "gap_count": len(gaps),
        "gap_days": {"minimum": min(gaps) if gaps else None,
                     "maximum": max(gaps) if gaps else None,
                     "varying": len(set(gaps)) > 1 if gaps else False},
        "stale_days": stale_days,
        "records": [record(item) for item in rows_ascending],
        "generated_at": now.isoformat(),
    }


def _format_range(facts: dict, lang: str) -> str:
    if facts["date_range"] == "all":
        return f"all available dates through {facts['range_end']} (Africa/Cairo)" if lang == "en" else f"كل التواريخ المتاحة حتى {facts['range_end']} (بتوقيت القاهرة)"
    days = facts["date_range"]
    return f"the last {days} days ({facts['range_start']} through {facts['range_end']}, Africa/Cairo)" if lang == "en" else f"آخر {days} يومًا ({facts['range_start']} إلى {facts['range_end']} بتوقيت القاهرة)"


def _validated_trend_explanation(message: str, facts: dict) -> str | None:
    prompt = json.dumps({
        "task": "explain_validated_measurement_insights",
        "question": message,
        "validated_calculations": {
            key: facts[key]
            for key in (
                "metric", "unit", "date_range", "range_start", "range_end",
                "measurement_count", "dates_covered", "latest", "previous",
                "absolute_change", "minimum", "maximum", "gap_days", "truncated", "stale_days",
            )
        },
    }, ensure_ascii=True, separators=(",", ":"))
    try:
        response = json.loads(generate_gemini_reply(prompt, TREND_EXPLANATION_SCHEMA))
    except (HTTPException, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(response, dict) or set(response) != {"explanation"}:
        return None
    explanation = response["explanation"]
    if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 350:
        return None
    explanation = explanation.strip()
    unsupported = re.search(
        r"\d|%|\b(?:improv\w*|worsen\w*|deterior\w*|normal|abnormal|high|low|good|bad|healthy|risk|diagnos\w*|target|treat\w*|recommend\w*|should|must)\b|(?:تحسن|تدهور|طبيعي|مرتفع|منخفض|جيد|سيء|خطر|تشخيص|هدف|علاج|ينبغي|يجب)",
        explanation,
        re.I,
    )
    if unsupported:
        return None
    has_arabic = bool(re.search(r"[\u0600-\u06ff]", explanation))
    if (_language(message) == "ar") != has_arabic:
        return None
    return explanation


def render_measurement_insights(
    facts: dict,
    lang: str = "en",
    explanation: str | None = None,
) -> str:
    labels = {"en": {"weight": "weight", "a1c": "HbA1c", "bp": "blood pressure"},
              "ar": {"weight": "الوزن", "a1c": "السكر التراكمي", "bp": "ضغط الدم"}}[lang]
    metric = facts["metric"]
    label = labels[metric]
    if not facts["measurement_count"]:
        return f"No saved {label} measurements were found for {_format_range(facts, lang)}." if lang == "en" else f"لم أجد قياسات محفوظة لـ{label} خلال {_format_range(facts, lang)}."

    latest, previous, earliest = facts["latest"], facts["previous"], facts["earliest"]
    unit = facts["unit"]
    if metric == "bp":
        latest_value = f"{latest['systolic']:g}/{latest['diastolic']:g} {unit}" if latest["diastolic"] is not None else f"{latest['systolic']:g} mmHg systolic; diastolic unavailable"
        lines = [f"Your latest available saved blood-pressure reading is {latest_value} on {latest['date']}."]
        if previous:
            previous_value = f"{previous['systolic']:g}/{previous['diastolic']:g}" if previous["diastolic"] is not None else f"{previous['systolic']:g}/unavailable"
            change = facts["absolute_change"]
            dia = f"{change['diastolic']:+g} mmHg" if change["diastolic"] is not None else "unavailable because one paired value is missing"
            lines.append(f"Previous saved reading: {previous_value} mmHg on {previous['date']}. Systolic change: {change['systolic']:+g} mmHg; diastolic change: {dia}.")
        minimum, maximum = facts["minimum"], facts["maximum"]
        if minimum["diastolic"] and maximum["diastolic"]:
            dia_range = f"diastolic values span {minimum['diastolic']['diastolic']:g}–{maximum['diastolic']['diastolic']:g} mmHg, each from its own paired reading"
        else:
            dia_range = "diastolic range unavailable in saved readings"
        lines.append(f"Within this range, systolic values span {minimum['systolic']['systolic']:g}–{maximum['systolic']['systolic']:g} mmHg; {dia_range}.")
    else:
        fmt = (lambda value: f"{value:g}%") if metric == "a1c" else (lambda value: f"{value:g} kg")
        lines = []
        if facts["measurement_count"] > 2:
            lines.append(f"Across this range, the earliest saved reading was {fmt(earliest['value'])} on {earliest['date']}, and the latest was {fmt(latest['value'])} on {latest['date']}.")
        if previous:
            change = facts["absolute_change"]
            change_unit = "percentage points" if metric == "a1c" else unit
            lines.append(f"Your saved {label} changed from {fmt(previous['value'])} on {previous['date']} to {fmt(latest['value'])} on {latest['date']} ({change:+g} {change_unit}).")
        else:
            lines.append(f"Your latest available saved {label} reading is {fmt(latest['value'])} on {latest['date']}.")
        minimum, maximum = facts["minimum"], facts["maximum"]
        lines.append(f"Within this range, the lowest recorded value was {fmt(minimum['value'])} on {minimum['date']}; the highest was {fmt(maximum['value'])} on {maximum['date']}.")

    if explanation:
        lines.append(f"In plain language: {explanation}" if lang == "en" else f"بعبارة مبسطة: {explanation}")

    start, end = facts["dates_covered"]["first"], facts["dates_covered"]["last"]
    lines.append(f"This graph range contains {facts['measurement_count']} measurement(s) across {start} to {end} ({_format_range(facts, lang)}).")
    if facts["gap_count"]:
        gap = facts["gap_days"]
        if gap["varying"]:
            lines.append(f"The {facts['gap_count']} gaps between saved dates vary from {gap['minimum']} to {gap['maximum']} days; this is not a steady rate of change.")
        else:
            lines.append(f"The {facts['gap_count']} gap(s) between saved dates are {gap['minimum']} days each; spacing alone does not establish a rate of change.")
    if facts["measurement_count"] == 1:
        lines.append("One reading is not enough to establish a trend.")
    elif facts["measurement_count"] == 2:
        lines.append("These two readings show a point-to-point numerical change, not a sustained trend.")
    else:
        lines.append("Numerical changes alone do not establish clinical improvement, deterioration, or cause.")
    if facts["stale_days"] is not None and facts["stale_days"] > 365:
        lines.append(f"The latest saved reading is {facts['stale_days']} days old; it may not describe your current health.")
    if facts["truncated"]:
        lines.append(f"This summary includes {facts['included_count']} of {facts['measurement_count']} readings in the selected range; older/additional readings may be omitted.")
    lines.append("I can describe how your readings changed, but I don’t have your clinician’s personal target to assess whether you have reached it. You could compare another period or prepare this summary for your clinician.")
    if lang == "ar":
        return "\n".join(lines)
    return _bound_reply("\n".join(lines))


def _is_comparison(message: str) -> bool:
    return bool(re.search(r"\b(?:compare|comparison|difference|change|changed|trend|between|since)\b|(?:قارن|مقارنة|الفرق|تغير|التغير|منذ)", message, re.I))


def _number(value: float) -> str:
    return f"{value:g}"


def _measurement_reply(message: str, context: dict, kind: str, compare: bool = False) -> str:
    lang = _language(message)
    entries = context["measurements"][kind]
    labels = {"en": {"weight": "Weight", "a1c": "HbA1c", "bp": "Blood pressure"},
              "ar": {"weight": "الوزن", "a1c": "السكر التراكمي", "bp": "ضغط الدم"}}[lang]
    arabic_prepositions = {"weight": "للوزن", "a1c": "للسكر التراكمي", "bp": "لضغط الدم"}
    if not entries:
        return f"لا أستطيع العثور على {labels[kind]} في السجلات المتاحة لهذه المحادثة." if lang == "ar" else f"I can't find {labels[kind]} in the records available to this chat."

    unit = {"weight": "kg", "a1c": "%", "bp": "mmHg"}[kind]
    if compare:
        if len(entries) < 2:
            return "لا توجد قراءتان محفوظتان من النوع نفسه للمقارنة." if lang == "ar" else f"I can't find two saved {labels[kind]} readings to compare."
        latest, previous = entries[0], entries[1]
        if kind == "bp":
            sys_change = latest["systolic"] - previous["systolic"]
            dia_change = (
                latest["diastolic"] - previous["diastolic"]
                if latest["diastolic"] is not None and previous["diastolic"] is not None
                else None
            )
            if lang == "ar":
                previous_bp = f"{previous['systolic']:g}/{previous['diastolic']:g}" if previous["diastolic"] is not None else f"{previous['systolic']:g}/غير متاح"
                latest_bp = f"{latest['systolic']:g}/{latest['diastolic']:g}" if latest["diastolic"] is not None else f"{latest['systolic']:g}/غير متاح"
                dia_text = f"{dia_change:+g} mmHg" if dia_change is not None else "غير متاح"
                return f"آخر قراءتين محفوظتين لضغط الدم: {previous_bp} mmHg في {previous['date']}، ثم {latest_bp} mmHg في {latest['date']}. التغير العددي: الانقباضي {sys_change:+g} mmHg والانبساطي {dia_text}."
            previous_bp = f"{previous['systolic']:g}/{previous['diastolic']:g}" if previous["diastolic"] is not None else f"{previous['systolic']:g}/unavailable"
            latest_bp = f"{latest['systolic']:g}/{latest['diastolic']:g}" if latest["diastolic"] is not None else f"{latest['systolic']:g}/unavailable"
            dia_text = f"{dia_change:+g} mmHg" if dia_change is not None else "difference unavailable"
            return f"Latest available saved blood-pressure readings: {previous_bp} mmHg on {previous['date']}, then {latest_bp} mmHg on {latest['date']}. Numeric differences: systolic {sys_change:+g} mmHg; diastolic {dia_text}."
        difference = latest["value"] - previous["value"]
        change_unit = "percentage points" if kind == "a1c" else unit
        if lang == "ar":
            value_unit = "%" if kind == "a1c" else " " + unit
            return f"آخر قراءتين محفوظتين {arabic_prepositions[kind]}: {_number(previous['value'])}{value_unit} في {previous['date']}، ثم {_number(latest['value'])}{value_unit} في {latest['date']}. الفرق العددي: {difference:+g} {('نقطة مئوية' if kind == 'a1c' else unit)}."
        return f"Latest available saved {labels[kind]} readings: {_number(previous['value'])} {unit} on {previous['date']}, then {_number(latest['value'])} {unit} on {latest['date']}. Numeric difference: {difference:+g} {change_unit}."

    latest = entries[0]
    if kind == "bp":
        value = f"{latest['systolic']:g}/{latest['diastolic']:g} {unit}" if latest["diastolic"] is not None else f"{latest['systolic']:g} mmHg systolic; diastolic unavailable"
    else:
        value = f"{_number(latest['value'])}{unit if kind == 'a1c' else ' ' + unit}"
    return f"آخر قراءة محفوظة متاحة {arabic_prepositions[kind]} هي {value} بتاريخ {latest['date']}." if lang == "ar" else f"Your latest available saved {labels[kind]} reading is {value}, dated {latest['date']}."


def _appointment_reply(message: str, context: dict) -> str:
    lang = _language(message)
    if re.search(r"\b(?:email|emailed|sent|delivery)\b|(?:بريد|إيميل|أُرسل)", message, re.I):
        return "حالة تسليم البريد الإلكتروني غير محفوظة في سجل المواعيد." if lang == "ar" else "Email delivery status is not stored in the appointment records available to this chat."
    if re.search(r"\b(?:book|cancel|reschedule|change)\b|(?:احجز|إلغاء|ألغِ|تغيير الموعد)", message, re.I):
        return "هذه المحادثة للقراءة فقط ولا تستطيع حجز موعد أو إلغاءه أو تغييره. جميع المواعيد هنا تجريبية." if lang == "ar" else "This chat is read-only and cannot book, cancel, or change an appointment. All appointments here are demos."
    appointment = context["next_upcoming_confirmed_appointment"]
    if not appointment:
        return "لا أجد موعدًا تجريبيًا مؤكدًا قادمًا في السجلات المتاحة لهذه المحادثة." if lang == "ar" else "I can't find an upcoming confirmed demo appointment in the records available to this chat."
    if lang == "ar":
        return f"الموعد التجريبي المؤكد القادم: {appointment['clinic']}، {appointment['date']} الساعة {appointment['time']} بتوقيت القاهرة. لم يتم التواصل مع العيادة، ولا يثبت هذا حجزًا حقيقيًا. حالة تسليم البريد غير محفوظة."
    return f"Your next upcoming confirmed demo appointment is with {appointment['clinic']} on {appointment['date']} at {appointment['time']} Africa/Cairo time. The clinic was not contacted; this is not a real reservation. Email delivery status is not persisted."


def _screening_reply(message: str, context: dict) -> str:
    lang = _language(message)
    if not context["screenings"]:
        return "لا أجد سجل فحص في السجلات المتاحة لهذه المحادثة." if lang == "ar" else "I can't find a screening record in the records available to this chat."
    item = context["screenings"][0]
    if lang == "ar":
        detail = f"نص التنبؤ المحفوظ (غير مُتحقق منه، حرفيًا): ‘{item['prediction']}’. لم يتأكد أنه راجعه طبيب." if item["prediction"] else "لا يوجد تنبؤ محفوظ."
        return f"سجل الفحص {item['id']} بتاريخ {item['date']}: الحالة {item['status']}. {detail} رفع الصورة لا يعني تحليلها أو تشخيصًا."
    detail = f"Saved prediction text (unverified, verbatim): ‘{item['prediction']}’. Clinician review is not established." if item["prediction"] else "No prediction is saved."
    return f"Screening record {item['id']} dated {item['date']}: status {item['status']}. {detail} An upload is not image analysis or a diagnosis."


def _progress_reply(message: str, context: dict) -> str:
    progress = context["profile"]["care_progress"]
    if _language(message) == "ar":
        awareness = "مُعلّم كمكتمل" if progress["awareness_completed_flag"] else "غير مُعلّم كمكتمل"
        results = progress["results_appointment_reference"] or "غير متاح"
        followup = progress["followup_appointment_reference"] or "غير متاح"
        return f"علامة إكمال التوعية: {awareness}. مرجع النتائج: {results}. مرجع المتابعة: {followup}. هذه العلامات لا تثبت حضورًا أو مراجعة طبية أو علاجًا."
    awareness = "marked complete" if progress["awareness_completed_flag"] else "not marked complete"
    results = progress["results_appointment_reference"] or "unavailable"
    followup = progress["followup_appointment_reference"] or "unavailable"
    return f"Saved care-progress flags: awareness {awareness}; results appointment reference {results}; follow-up reference {followup}. These flags do not prove attendance, clinician review, or treatment."


def _all_record_map(context: dict) -> dict[str, dict]:
    records = {}
    for entries in context["measurements"].values():
        records.update((item["id"], item) for item in entries)
    records.update((item["id"], item) for item in context["screenings"])
    records.update((item["id"], item) for item in context["appointments"])
    upcoming = context["next_upcoming_confirmed_appointment"]
    if upcoming:
        records[upcoming["id"]] = upcoming
    records.update((item["id"], item) for item in context["profile"].values())
    return records


def _summary_candidate_ids(message: str, context: dict) -> set[str]:
    kinds = _mentioned_measurement_kinds(message)
    candidates = set()
    records = _all_record_map(context)
    if kinds:
        for kind in kinds:
            candidates.update(item["id"] for item in context["measurements"][kind])
    else:
        candidates.update(item["id"] for entries in context["measurements"].values() for item in entries)

    wants_screenings = bool(re.search(r"\b(?:screening|screenings|retinal|prediction|result)\b|(?:فحص|فحوصات|الشبكية|نتيجة)", message, re.I))
    wants_appointments = bool(re.search(r"\b(?:appointment|appointments|booking|visit)\b|(?:موعد|مواعيد|حجز)", message, re.I))
    wants_measurements = bool(re.search(r"\b(?:measurements?|readings?)\b|(?:قياسات|قراءاتي|القراءات)", message, re.I))
    if wants_screenings:
        candidates.update(item["id"] for item in context["screenings"])
    if wants_appointments:
        candidates.update(item["id"] for item in context["appointments"])
        upcoming = context["next_upcoming_confirmed_appointment"]
        if upcoming:
            candidates.add(upcoming["id"])

    if re.search(r"\b(?:diabetes type|type of diabetes)\b|(?:نوع السكري)", message, re.I):
        candidates.add("profile:diabetes_type")
    if re.search(r"\b(?:care progress|progress)\b|(?:تقدم الرعاية|التقدم)", message, re.I):
        candidates.add("profile:care_progress")

    has_specific_category = bool(kinds or wants_measurements or wants_screenings or wants_appointments or
                                 "profile:diabetes_type" in candidates or "profile:care_progress" in candidates)
    if not has_specific_category:
        candidates.update(item["id"] for item in records.values())
    return candidates


def _summary_candidate(item: dict) -> dict:
    allowed_fields = ("id", "source", "kind", "date", "status", "reference", "clinic", "booking_type")
    return {key: item[key] for key in allowed_fields if key in item}


def _summary_selection(prompt: str, valid_ids: set[str]) -> list[str] | None:
    try:
        selection = json.loads(generate_gemini_reply(prompt))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(selection, dict) or set(selection) != {"record_ids"}:
        return None
    record_ids = selection["record_ids"]
    if not isinstance(record_ids, list) or not record_ids or len(record_ids) > SUMMARY_LIMIT:
        return None
    if any(not isinstance(record_id, str) or record_id not in valid_ids for record_id in record_ids):
        return None
    if len(set(record_ids)) != len(record_ids):
        return None
    return record_ids


def _render_summary(message: str, context: dict, record_ids: list[str]) -> str:
    lang = _language(message)
    records = _all_record_map(context)
    lines = []
    for record_id in record_ids:
        item = records[record_id]
        if record_id.startswith("measurement:"):
            if item["kind"] == "bp":
                line = f"BP {item['systolic']:g}/{item['diastolic']:g} mmHg on {item['date']}" if lang == "en" else f"ضغط الدم {item['systolic']:g}/{item['diastolic']:g} mmHg بتاريخ {item['date']}"
            else:
                label = "Weight" if item["kind"] == "weight" else "HbA1c"
                arabic_label = "الوزن" if item["kind"] == "weight" else "السكر التراكمي"
                separator = "" if item["kind"] == "a1c" else " "
                line = f"{label} {_number(item['value'])}{separator}{item['unit']} on {item['date']}" if lang == "en" else f"{arabic_label} {_number(item['value'])}{separator}{item['unit']} بتاريخ {item['date']}"
            lines.append(line)
        elif record_id.startswith("screening:"):
            prediction = f"Saved prediction text (unverified, verbatim): ‘{item['prediction']}’" if item["prediction"] else "No prediction saved"
            line = f"Screening {record_id} on {item['date']}: status {item['status']}; {prediction}; upload is not analysis or diagnosis"
            if lang == "ar":
                prediction = f"نص التنبؤ المحفوظ (غير مُتحقق منه، حرفيًا): ‘{item['prediction']}’" if item["prediction"] else "لا يوجد تنبؤ محفوظ"
                line = f"الفحص {record_id} بتاريخ {item['date']}: الحالة {item['status']}؛ {prediction}؛ الرفع ليس تحليلًا أو تشخيصًا"
            lines.append(line)
        elif record_id.startswith("appointment:"):
            line = f"Demo appointment {item['reference']} with {item['clinic']} on {item['date']} at {item['time']} Africa/Cairo; status {item['status']}; clinic not contacted; email delivery status not persisted"
            if lang == "ar":
                line = f"الموعد التجريبي {item['reference']} مع {item['clinic']} بتاريخ {item['date']} الساعة {item['time']} بتوقيت القاهرة؛ الحالة {item['status']}؛ لم يتم التواصل مع العيادة؛ حالة البريد غير محفوظة"
            lines.append(line)
        elif record_id == "profile:diabetes_type":
            lines.append(f"Saved diabetes type: {item['value']}" if lang == "en" else f"نوع السكري المحفوظ: {item['value']}")
        elif record_id == "profile:care_progress":
            lines.append(_progress_reply(message, context))

    if not lines:
        return "تعذر تحديد سجلات موثوقة لهذا الملخص. اسأل عن قراءة أو موعد محدد." if lang == "ar" else "I couldn't identify reliable records for that summary. Ask about a specific reading or appointment."
    header = "ملخص السجلات المحددة:" if lang == "ar" else "Summary of selected saved records:"
    coverage_notes = []
    for kind, coverage in context["coverage"]["measurements"].items():
        selected_kind = any(records[rid]["kind"] == kind for rid in record_ids if rid in records and rid.startswith("measurement:"))
        if coverage["truncated"] and selected_kind:
            coverage_notes.append(f"Older {kind} records omitted ({coverage['included_count']} of {coverage['total_count']} included)." if lang == "en" else f"سجلات {kind} الأقدم غير مضمّنة ({coverage['included_count']} من {coverage['total_count']}).")
    for category in ("screenings", "appointments"):
        coverage = context["coverage"][category]
        prefix = "screening:" if category == "screenings" else "appointment:"
        selected_category = any(record_id.startswith(prefix) for record_id in record_ids)
        if coverage["truncated"] and selected_category:
            coverage_notes.append(
                f"Older {category} omitted ({coverage['included_count']} of {coverage['total_count']} included)."
                if lang == "en" else
                f"السجلات الأقدم من فئة {category} غير مضمّنة ({coverage['included_count']} من {coverage['total_count']})."
            )
    return _bound_reply("\n".join([header, *lines, *coverage_notes]))


def _bound_reply(reply: str) -> str:
    if len(reply) <= MAX_REPLY_LENGTH:
        return reply
    arabic = _language(reply) == "ar"
    suffix = "\nسجلات إضافية غير معروضة؛ اسأل عن سجل محدد." if arabic else "\nAdditional records omitted; ask about a specific item."
    kept = []
    for line in reply.splitlines():
        if len("\n".join([*kept, line, suffix])) > MAX_REPLY_LENGTH:
            break
        kept.append(line)
    if not kept:
        return "الملخص طويل جدًا للعرض. اسأل عن سجل محدد." if arabic else "The available summary is too long to display. Ask about a specific record."
    return "\n".join([*kept, suffix])


def _is_summary_request(message: str) -> bool:
    summary = re.search(r"\b(?:summari[sz]e|summary|overview)\b|(?:لخص|ملخص|نظرة عامة)", message, re.I)
    personal_records = re.search(
        r"\b(?:my|saved|personal|account|records?|data|health|measurements?|readings?|appointments?|screenings?|results?|hba1c|a1c|weight|blood pressure|bp)\b|"
        r"(?:سجلاتي|بياناتي|المحفوظة|محفوظة|قياساتي|قراءاتي|مواعيدي|فحوصاتي|نتائجي|السكر التراكمي|الوزن|ضغط الدم)",
        message,
        re.I,
    )
    return bool(summary and personal_records)


def _fallback_reply(message: str) -> str:
    return "يمكنني البحث في سجلاتك المحفوظة فقط. لا أملك محتوى طبيًا تثقيفيًا مُراجعًا للإجابة عن هذا السؤال." if _language(message) == "ar" else "I can look up your saved records only. Reviewed educational content is not available to answer this question."


def answer_patient_question(message: str, context: dict) -> str:
    lang = _language(message)
    urgent = urgent_symptom_status(message)
    if urgent:
        return _urgent_reply(message, urgent)
    if _is_medication_dose_request(message):
        return "لا أستطيع تقديم نصيحة بشأن جرعة الدواء أو الإنسولين. تواصل مع فريقك الطبي قبل تغيير أي جرعة." if lang == "ar" else "I can't advise on medication or insulin doses. Contact your care team before changing a dose."
    if _asks_about_another_patient(message):
        return "لا أستطيع الوصول إلى سجلات مريض آخر. يمكنني المساعدة فقط في السجلات المرتبطة بحسابك." if lang == "ar" else "I can't access another patient's records. I can help only with records linked to your account."
    if _is_summary_request(message):
        return ""

    if ENGLISH_DEFINITION.search(message) or ARABIC_DEFINITION.search(message):
        kind = _measurement_kind(message)
        definitions = {
            "weight": ("Weight is recorded in kilograms (kg). Reviewed educational content about interpreting weight is unavailable." if lang == "en" else "يُسجَّل الوزن بالكيلوغرام (kg). لا يتوفر محتوى تثقيفي مُراجع لتفسير الوزن."),
            "a1c": ("HbA1c is stored as a percentage (%). Reviewed educational content explaining its clinical meaning is unavailable." if lang == "en" else "يُسجَّل السكر التراكمي كنسبة مئوية (%). لا يتوفر محتوى تثقيفي مُراجع لشرح دلالته الطبية."),
            "bp": ("Blood pressure is stored as systolic/diastolic millimeters of mercury (mmHg). Reviewed educational content about its clinical meaning is unavailable." if lang == "en" else "يُسجَّل ضغط الدم كقيمة انقباضية/انبساطية بوحدة ملم زئبق (mmHg). لا يتوفر محتوى تثقيفي مُراجع لشرح دلالته الطبية."),
        }
        if kind:
            return definitions[kind]
        return _fallback_reply(message)

    mentioned_kinds = _mentioned_measurement_kinds(message)
    if len(mentioned_kinds) > 1:
        return "Which measurement would you like me to check: weight, HbA1c, or blood pressure?" if lang == "en" else "أي قياس تريد مراجعته: الوزن أم السكر التراكمي أم ضغط الدم؟"
    kind = next(iter(mentioned_kinds), None)
    if kind:
        return _measurement_reply(message, context, kind, _is_comparison(message))
    if re.search(r"\b(?:appointment|booking|booked|when is my visit)\b|(?:موعد|مواعيد|حجز)", message, re.I):
        return _appointment_reply(message, context)
    if re.search(r"\b(?:screening|screenings|retinal image|prediction|result)\b|(?:فحص|فحوصات|صورة الشبكية|نتيجة)", message, re.I):
        return _screening_reply(message, context)
    if re.search(r"\b(?:care progress|progress flags|follow.?up progress)\b|(?:تقدم الرعاية|التقدم|المتابعة)", message, re.I):
        return _progress_reply(message, context)
    if re.search(r"\b(?:diabetes type|type of diabetes|my diabetes type)\b|(?:نوع السكري|نوع مرض السكري)", message, re.I):
        value = context["profile"]["diabetes_type"]["value"]
        if not value or value.casefold() in {"not specified", "unknown / not sure"}:
            return "لا أجد نوعًا محددًا في السجلات المتاحة لهذه المحادثة." if lang == "ar" else "I can't find a specified diabetes type in the records available to this chat."
        return f"نوع السكري المحفوظ في ملفك هو: {value}. هذه قيمة ملف محفوظة وليست تحققًا طبيًا مستقلًا." if lang == "ar" else f"Your saved profile lists diabetes type: {value}. This is a saved profile field, not an independently verified diagnosis."
    if re.search(r"\b(?:what records|which records|what data|my saved data)\b|(?:ما السجلات|ما البيانات|بياناتي المحفوظة)", message, re.I):
        coverage = context["coverage"]
        if lang == "ar":
            return f"تتضمن السجلات المتاحة قياسات محفوظة: وزن {coverage['measurements']['weight']['total_count']}، وسكر تراكمي {coverage['measurements']['a1c']['total_count']}، وضغط دم {coverage['measurements']['bp']['total_count']}؛ وفحوصات {coverage['screenings']['total_count']} ومواعيد تجريبية {coverage['appointments']['total_count']}."
        return f"Available saved records: weight {coverage['measurements']['weight']['total_count']}, HbA1c {coverage['measurements']['a1c']['total_count']}, blood pressure {coverage['measurements']['bp']['total_count']}; screenings {coverage['screenings']['total_count']}; demo appointments {coverage['appointments']['total_count']}."
    return _fallback_reply(message)


@router.post("", response_model=PatientChatResponse)
def chat_about_patient_records(
    payload: PatientChatRequest,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Consent is required to send record context to Gemini")

    context = patient_record_context(patient, db)
    if _is_graph_insight_question(payload.message):
        chart_context = payload.chart_context
        if chart_context and chart_context.example_preview:
            reply = "The visible chart is showing illustrative preview data, not your saved measurements. Turn off the example preview or ask about a saved chart to review your account history."
            return PatientChatResponse(reply=reply)
        metric, clarification = _chart_metric_for_question(payload.message, chart_context)
        if clarification:
            visible = chart_context.visible_metrics if chart_context else []
            if len(visible) > 1 and not _mentioned_measurement_kinds(payload.message):
                return PatientChatResponse(reply="I can see weight, HbA1c, and blood-pressure graphs. Which one should I review?")
            return PatientChatResponse(reply=clarification)
        date_range = chart_context.date_range if chart_context else "all"
        facts = measurement_insights(patient, db, metric, date_range)
        explanation = _validated_trend_explanation(payload.message, facts)
        return PatientChatResponse(reply=_bound_reply(render_measurement_insights(
            facts, _language(payload.message), explanation
        )))

    answer = answer_patient_question(payload.message, context)
    if answer:
        return PatientChatResponse(reply=_bound_reply(answer))

    candidate_ids = _summary_candidate_ids(payload.message, context)
    record_map = _all_record_map(context)
    prompt = json.dumps({
        "task": "select_evidence_records_for_summary",
        "question": payload.message,
        "record_candidates": [_summary_candidate(record_map[record_id]) for record_id in sorted(candidate_ids)],
    }, ensure_ascii=True, separators=(",", ":"))
    record_ids = _summary_selection(prompt, candidate_ids)
    if record_ids is None:
        fallback = "تعذر التحقق من مراجع السجلات المطلوبة للملخص. اسأل عن قراءة أو موعد محدد." if _language(payload.message) == "ar" else "I couldn't validate the record references for that summary. Ask about a specific reading or appointment."
        return PatientChatResponse(reply=fallback)
    return PatientChatResponse(reply=_render_summary(payload.message, context, record_ids))