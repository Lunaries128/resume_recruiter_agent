import json
from pathlib import Path

import requests


API_URL = "http://127.0.0.1:8000"

TASK_FILE = (
    Path(__file__).parent
    / "tasks.jsonl"
)


def load_tasks() -> list[dict]:
    tasks = []

    with TASK_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line in file:
            if line.strip():
                tasks.append(
                    json.loads(line)
                )

    return tasks


def keyword_score(
    response_text: str,
    expected: list[str],
) -> float:
    if not expected:
        return 1.0

    matched = sum(
        1
        for keyword in expected
        if keyword.lower()
        in response_text.lower()
    )

    return matched / len(expected)


def run_chat_task(
    task: dict,
) -> dict:
    response = requests.post(
        f"{API_URL}/chat",
        json={
            "session_id": (
                f"eval-{task['id']}"
            ),
            "hr_id": "eval_hr",
            "message": task["input"],
            "jd": (
                "Python算法工程师，"
                "要求3年以上Python经验，"
                "熟悉推荐系统、SQL和PyTorch，"
                "本科以上。"
            ),
        },
        timeout=300,
    )

    text = response.text

    return {
        "id": task["id"],
        "category": task["category"],
        "http_ok": response.ok,
        "keyword_score": (
            keyword_score(
                text,
                task["expected"],
            )
        ),
        "response_preview": text[:500],
    }


def main():
    tasks = load_tasks()
    results = []

    for task in tasks:
        try:
            result = run_chat_task(task)

        except Exception as error:
            result = {
                "id": task["id"],
                "category": (
                    task["category"]
                ),
                "http_ok": False,
                "keyword_score": 0,
                "error": str(error),
            }

        results.append(result)

        print(
            task["id"],
            result["keyword_score"],
        )

    success_rate = (
        sum(
            1
            for result in results
            if (
                result["http_ok"]
                and result[
                    "keyword_score"
                ] >= 0.5
            )
        )
        / len(results)
    )

    category_scores = {}

    for result in results:
        category_scores.setdefault(
            result["category"],
            [],
        ).append(
            result["keyword_score"]
        )

    category_summary = {
        category: round(
            sum(scores) / len(scores),
            4,
        )
        for category, scores
        in category_scores.items()
    }

    report = {
        "task_count": len(results),
        "success_rate": round(
            success_rate,
            4,
        ),
        "category_scores": (
            category_summary
        ),
        "results": results,
    }

    output_path = (
        Path(__file__).parent
        / "evaluation_result.json"
    )

    output_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "task_count": (
                    len(results)
                ),
                "success_rate": (
                    success_rate
                ),
                "category_scores": (
                    category_summary
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()