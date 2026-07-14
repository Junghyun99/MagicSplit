"""docs/ 정적 UI에서 참조하는 CSS 변수가 모두 정의되어 있는지 검증한다.

HTML 인라인 스타일이 오타난 변수명(예: var(--bg-card) vs 정의된 --card-bg)을
참조하면 브라우저에서 조용히 무효값이 되어 배경이 투명해지는 등의 UI 버그가
난다. scripts/check_css_vars.py 로 사전 차단하며, 이 테스트로 CI에 편입한다.
"""
import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = os.path.join(_ROOT, "docs")


def _load_checker():
    path = os.path.join(_ROOT, "scripts", "check_css_vars.py")
    spec = importlib.util.spec_from_file_location("check_css_vars", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_undefined_css_vars():
    checker = _load_checker()
    undefined = checker.find_undefined(_DOCS)
    detail = "\n".join(
        f"  {os.path.relpath(path, _ROOT)}:{ln}  var({name})"
        for name, path, ln in undefined
    )
    assert not undefined, (
        f"docs/ 에서 정의되지 않은 CSS 변수 참조 {len(undefined)}건 발견 "
        f"(오타 또는 :root 정의 누락):\n{detail}"
    )
