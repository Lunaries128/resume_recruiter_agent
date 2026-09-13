from langchain_core.tools import tool

from memory import save_preference


@tool
def remember_hr_preference(
    hr_id: str,
    preference: str,
) -> dict:
    """
    保存HR明确要求长期记住的岗位相关偏好。

    不得保存年龄、性别、婚育、民族等
    敏感筛选条件。
    """

    return save_preference(
        hr_id=hr_id,
        preference=preference,
    )