from hr_workflow import (
    preferences,
    save_preference as _save,
)


def save_preference(
    hr_id: str,
    preference: str,
) -> dict:
    return _save(
        hr_id,
        preference,
        "通用",
    )


def retrieve_preferences(
    hr_id: str,
    query: str = "",
    k: int = 4,
) -> list[str]:
    # 兼容旧调用；新版Agent按明确岗位类别读取。
    return [
        item["content"]
        for item in preferences(hr_id, "通用")
    ][:k]


save_hr_preference = save_preference
search_hr_preferences = retrieve_preferences