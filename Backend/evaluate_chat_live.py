import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from chat_prompts import SYSTEM_INSTRUCTION_VERSION
from routers.chat import SUMMARY_SELECTION_SCHEMA, generate_gemini_reply


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in synthetic Gemini chat evidence-selection smoke evaluation.")
    parser.add_argument("--run", action="store_true", help="Send synthetic evaluation prompts to the configured Gemini model.")
    args = parser.parse_args()
    if not args.run:
        parser.error("No API calls are made by default. Pass --run to execute the synthetic live evaluation.")
    if not os.getenv("GEMINI_API_KEY") or not os.getenv("GEMINI_MODEL"):
        parser.error("Set GEMINI_API_KEY and GEMINI_MODEL before opting in.")

    cases = json.loads(Path(__file__).with_name("chat_live_eval.json").read_text(encoding="utf-8"))
    max_items = SUMMARY_SELECTION_SCHEMA["properties"]["record_ids"]["maxItems"]
    failures = 0
    for case in cases:
        prompt = json.dumps({
            "task": "select_evidence_records_for_summary",
            "question": case["question"],
            "authenticated_patient_records": case["records"],
        }, ensure_ascii=True, separators=(",", ":"))
        try:
            output = json.loads(generate_gemini_reply(prompt))
            record_ids = output.get("record_ids") if isinstance(output, dict) else None
            available_ids = {
                item["id"]
                for group in case["records"]["measurements"].values()
                for item in group
            }
            available_ids.update(item["id"] for item in case["records"]["screenings"])
            available_ids.update(item["id"] for item in case["records"]["appointments"])
            valid = (
                isinstance(record_ids, list)
                and len(record_ids) <= max_items
                and len(record_ids) == len(set(record_ids))
                and all(isinstance(record_id, str) and record_id in available_ids for record_id in record_ids)
                and set(case["expected_record_ids"]).issubset(record_ids)
            )
        except Exception as error:
            print(f"FAIL {case['name']}: {type(error).__name__}")
            failures += 1
            continue
        print(f"{'PASS' if valid else 'FAIL'} {case['name']}: selected {record_ids}")
        failures += int(not valid)

    print(f"Completed {len(cases)} synthetic cases; {failures} failed.")
    print(f"System instruction version: {SYSTEM_INSTRUCTION_VERSION}")
    print("This smoke evaluation does not prove the model follows instructions or establish clinical safety.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
