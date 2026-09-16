"""独立子进程：可被超时终止，且永远不写候选人数据库。"""
import contextlib
import json
import sys


if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from tools.extract_tool import extract_document
            result = {"success": True, **extract_document(sys.argv[1])}
    except Exception as exc:
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
