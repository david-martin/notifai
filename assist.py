import json
import sys

import anthropic
import yaml

from core import ASSIST_MODEL, ASSIST_SYSTEM_PROMPT, parse_response

QUERIES_PATH = "queries.yaml"


def check_duplicate_id(id_: str, path: str = QUERIES_PATH) -> bool:
    with open(path) as f:
        data = yaml.safe_load(f)
    return any(q["id"] == id_ for q in data.get("queries", []))


def append_query(entry: dict, path: str = QUERIES_PATH) -> None:
    with open(path) as f:
        data = yaml.safe_load(f)
    data["queries"].append({**entry, "active": True})
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def prompt_edit(entry: dict) -> dict:
    print("Edit each field (press Enter to keep current value):")
    for key in ("id", "description", "query"):
        val = input(f"  {key} [{entry[key]}]: ").strip()
        if val:
            entry[key] = val
    return entry


def run():
    client = anthropic.Anthropic()

    print("Notify me when…")
    print("(describe the event in plain English — the AI will reword it for you)")
    description = input("> ").strip()
    if not description:
        print("Nothing entered. Exiting.")
        sys.exit(0)

    response = client.messages.create(
        model=ASSIST_MODEL,
        max_tokens=512,
        system=ASSIST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": description}],
    )

    text_block = next((b.text for b in response.content if hasattr(b, "text")), None)
    if text_block is None:
        print("No response from Claude. Exiting.")
        sys.exit(1)

    try:
        entry = parse_response(text_block)
    except json.JSONDecodeError:
        print(f"Malformed response from Claude: {text_block!r}")
        sys.exit(1)

    while True:
        print("\nProposed query:")
        print(f"  id:          {entry['id']}")
        print(f"  description: {entry['description']}")
        print(f"  Notify me when… {entry['query']}")

        action = input("\n[y] confirm  [e] edit  [n] discard: ").strip().lower()

        if action == "y":
            if check_duplicate_id(entry["id"]):
                print(f"  ID '{entry['id']}' already exists. Enter a new id:")
                entry["id"] = input("  id: ").strip()
                continue
            append_query(entry)
            print(f"Added '{entry['id']}' to queries.yaml.")
            break
        elif action == "e":
            entry = prompt_edit(entry)
        elif action == "n":
            print("Discarded.")
            break
        else:
            print("Please enter y, e, or n.")


if __name__ == "__main__":
    run()
