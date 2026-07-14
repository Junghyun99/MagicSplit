#!/usr/bin/env python3
"""docs/ 정적 UI에서 사용된 CSS 변수(var(--x))가 어딘가에 정의(--x:)되어
있는지 검사한다.

HTML 인라인 스타일이 오타난 변수명(예: var(--bg-card))을 참조하면 브라우저에서
무효값이 되어 배경이 투명해지는 등 조용히 깨진다. 이를 CI에서 사전 차단한다.

- 정의 수집: docs/**/*.css, docs/**/*.html 의 `--이름:` (CSS custom property)
- 참조 수집: docs/**/*.css, docs/**/*.html, docs/**/*.js 의 `var(--이름)`
  (JS는 인라인 style 문자열에서 CSS 변수를 참조하기도 한다)

정의되지 않은 참조가 하나라도 있으면 목록을 출력하고 exit 1.

사용법:
    python scripts/check_css_vars.py            # docs/ 검사
    python scripts/check_css_vars.py <dir>      # 임의 디렉토리 검사
"""
import os
import re
import sys
from typing import Dict, List, Set, Tuple

DEF_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
REF_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

DEF_EXTS = (".css", ".html")
REF_EXTS = (".css", ".html", ".js")


def _blank_comment(m: "re.Match") -> str:
    """주석을 제거하되 개행 수는 보존해 이후 라인 번호가 밀리지 않게 한다."""
    return "\n" * m.group(0).count("\n")


def _read(path: str) -> str:
    """파일을 관대하게 읽고 NUL/주석을 제거해 오탐을 줄인다."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    text = text.replace("\x00", "")  # 일부 파일에 섞인 NUL(UTF-16 잔재) 제거
    # 주석 안의 var() 는 실제 참조가 아니므로 제거 (CSS 블록 / HTML 주석).
    # 여러 줄 주석은 개행을 보존해 참조의 라인 번호 정확도를 유지한다.
    text = BLOCK_COMMENT_RE.sub(_blank_comment, text)
    text = HTML_COMMENT_RE.sub(_blank_comment, text)
    return text


def _iter_files(root: str, exts: Tuple[str, ...]):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(exts):
                yield os.path.join(dirpath, name)


def scan(docs_dir: str) -> Tuple[Set[str], List[Tuple[str, str, int]]]:
    """정의된 변수 집합과 (변수, 파일, 라인) 참조 목록을 반환한다."""
    defined: Set[str] = set()
    for path in _iter_files(docs_dir, DEF_EXTS):
        for m in DEF_RE.finditer(_read(path)):
            defined.add(m.group(1))

    refs: List[Tuple[str, str, int]] = []
    for path in _iter_files(docs_dir, REF_EXTS):
        text = _read(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in REF_RE.finditer(line):
                refs.append((m.group(1), path, lineno))
    return defined, refs


def find_undefined(docs_dir: str) -> List[Tuple[str, str, int]]:
    """정의되지 않은 변수를 참조하는 (변수, 파일, 라인) 목록을 반환한다."""
    defined, refs = scan(docs_dir)
    return [(name, path, ln) for (name, path, ln) in refs if name not in defined]


def main() -> int:
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else "docs"
    if not os.path.isdir(docs_dir):
        print(f"에러: 디렉토리를 찾을 수 없습니다: {docs_dir}")
        return 2

    defined, refs = scan(docs_dir)
    undefined = [(name, path, ln) for (name, path, ln) in refs if name not in defined]
    if not undefined:
        print(f"OK: CSS 변수 검사 통과 (정의 {len(defined)}개, 참조 {len(refs)}건, 미정의 0건)")
        return 0

    print(f"실패: 정의되지 않은 CSS 변수 참조 {len(undefined)}건 발견\n")
    for name, path, ln in undefined:
        print(f"  {path}:{ln}  var({name})  <- '{name}' 정의 없음")
    print("\n오타이거나 css/style.css의 :root 정의 누락일 수 있습니다. 변수명을 확인하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
