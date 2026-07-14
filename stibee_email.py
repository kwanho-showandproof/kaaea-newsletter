"""
====================================================================
스티비 이메일 초안 자동 작성 (STEP 5 / ① 단계)
====================================================================
역할: 오늘 data JSON의 데일리 모니터링 뉴스를 읽어,
      단순 HTML 이메일 본문으로 구성하고,
      스티비에 "작성 중" 이메일 초안을 생성한다.
      → 이후 협회 담당자가 스티비에서 검토·편집·발송한다.

주의(이번 버전):
  디자인 없이 단순 HTML로 만든다. (작동 확인용)
  협회 템플릿 디자인(헤더/푸터/캐릭터)은 나중에 입힌다.

스티비 API 흐름:
  1) POST /v2/emails               → 이메일 틀 생성 (listId·senderEmail·senderName·subject)
                                      응답으로 생성된 이메일 id 받음
  2) POST /v2/emails/{id}/content  → 그 id에 HTML 본문 삽입
                                      (Content-Type: text/html, body=HTML 통짜)
  인증: 모든 요청에 AccessToken 헤더.

계정 종속 값 (개인→협회 전환 시 이것만 교체, 코드는 그대로):
  - STIBEE_API_KEY   : 스티비 API 키 (Secrets/.env)
  - STIBEE_LIST_ID   : 발송 대상 주소록 ID
  - STIBEE_SENDER_EMAIL : 발신자 이메일 (스티비 등록된 주소)
  - STIBEE_SENDER_NAME  : 발신자 이름

보안:
  API 키·발신자 정보는 코드에 직접 쓰지 않는다. Secrets/.env 로만 주입.

라이브러리:
  pip install requests python-dotenv

사용법:
  python stibee_email.py            (오늘 data 기준, 실제 생성)
  python stibee_email.py 2026-07-08 (특정 날짜)
  python stibee_email.py --dry-run  (API 호출 없이 HTML만 파일로 출력해 확인)
====================================================================
"""
import os
import sys
import json
import glob
import html
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")

BASE_URL = "https://api.stibee.com/v2"
API_KEY = os.getenv("STIBEE_API_KEY")

# 계정 종속 값 (환경변수/Secrets)
LIST_ID = os.getenv("STIBEE_LIST_ID")
SENDER_EMAIL = os.getenv("STIBEE_SENDER_EMAIL")
SENDER_NAME = os.getenv("STIBEE_SENDER_NAME", "한국자동차환경협회")

# 데일리 모니터링 카테고리 순서 (웹진과 동일)
DAILY_ORDER = [
    "한국자동차환경협회 뉴스",
    "상위기관 뉴스",
    "배출 저감사업 뉴스",
    "전기·수소차 뉴스 - 협회 사업 관련 뉴스",
    "전기·수소차 뉴스 - 업계 동향",
    "회원사 뉴스",
    "기타 뉴스",
]


def esc(t):
    return html.escape(str(t), quote=True)


def build_email_html(data):
    """데일리 모니터링을 단순 HTML 이메일 본문으로 구성 (디자인 없음)."""
    date_str = data.get("date", "")
    dow = data.get("dow", "")
    daily = data.get("daily", {})

    # 카테고리 순서대로, ' - ' 하위카테고리는 상위로 묶어 소제목 처리
    parts = []
    rendered = set()

    def render_articles(articles):
        rows = ""
        for art in articles:
            title = esc(art.get("title", ""))
            url = art.get("url", "").strip()
            source = esc(art.get("source", ""))
            adate = esc(art.get("date", ""))
            summary = esc(art.get("summary", ""))
            title_html = f'<a href="{esc(url)}">{title}</a>' if url else title
            meta = " · ".join(x for x in [source, adate] if x)
            rows += (
                f'<div style="margin:0 0 16px 0;">'
                f'<div style="font-size:15px;font-weight:bold;margin-bottom:4px;">{title_html}</div>'
                f'<div style="font-size:13px;color:#333;line-height:1.6;">{summary}</div>'
                + (f'<div style="font-size:12px;color:#888;margin-top:3px;">{meta}</div>' if meta else "")
                + "</div>\n"
            )
        return rows

    for cat_name in DAILY_ORDER:
        if cat_name in rendered:
            continue
        if " - " in cat_name:
            parent = cat_name.split(" - ", 1)[0]
            subs = [c for c in DAILY_ORDER if c.startswith(parent + " - ") and daily.get(c)]
            if subs:
                parts.append(f'<h2 style="font-size:17px;background:#1F4E79;color:#fff;padding:10px 14px;margin:24px 0 0 0;">{esc(parent)}</h2>')
                for sc in subs:
                    parts.append(f'<h3 style="font-size:14px;color:#1F4E79;margin:14px 0 8px 0;">{esc(sc.split(" - ",1)[1])}</h3>')
                    parts.append(render_articles(daily[sc]))
                    rendered.add(sc)
        else:
            if daily.get(cat_name):
                parts.append(f'<h2 style="font-size:17px;background:#1F4E79;color:#fff;padding:10px 14px;margin:24px 0 12px 0;">{esc(cat_name)}</h2>')
                parts.append(render_articles(daily[cat_name]))
                rendered.add(cat_name)

    body = "\n".join(parts) or '<p>오늘 게시된 뉴스가 없습니다.</p>'

    return f"""<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Malgun Gothic',sans-serif;max-width:640px;margin:0 auto;padding:20px;color:#1a1a1a;">
<h1 style="font-size:22px;text-align:center;border-bottom:2px solid #1F4E79;padding-bottom:12px;">뉴스 모니터링</h1>
<p style="text-align:center;color:#555;font-size:14px;">{esc(date_str)} ({esc(dow)})</p>
{body}
<hr style="margin-top:30px;border:none;border-top:1px solid #ddd;">
<p style="font-size:12px;color:#888;text-align:center;">한국자동차환경협회 뉴스레터</p>
</body>
</html>"""


def create_email(subject):
    """이메일 틀 생성 → 생성된 이메일 id 반환."""
    payload = {
        "listId": int(LIST_ID),
        "senderEmail": SENDER_EMAIL,
        "senderName": SENDER_NAME,
        "subject": subject,
    }
    resp = requests.post(
        f"{BASE_URL}/emails",
        headers={"AccessToken": API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("id")


def set_content(email_id, html_body):
    """이메일 본문(HTML) 삽입."""
    resp = requests.post(
        f"{BASE_URL}/emails/{email_id}/content",
        headers={"AccessToken": API_KEY, "Content-Type": "text/html"},
        data=html_body.encode("utf-8"),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


def load_data(date_str=None):
    if date_str:
        path = os.path.join(DATA_DIR, f"{date_str}.json")
    else:
        files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
        if not files:
            print("[오류] data 폴더에 JSON이 없습니다.")
            sys.exit(1)
        path = files[-1]
    if not os.path.exists(path):
        print(f"[오류] 데이터 파일 없음: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv          # 날짜 불일치여도 강제 진행(테스트용)
    date_str = args[0] if args else None

    data = load_data(date_str)
    d = data.get("date", "")

    # ── 안전장치(방법 A): 오늘(KST) 날짜의 웹진인지 확인 ──
    # 오늘 data가 아니면 오래된 뉴스로 이메일이 나가는 사고를 막기 위해 중단한다.
    # - 날짜를 인자로 직접 지정했거나(수동 테스트), --force 를 주면 이 검사를 건너뛴다.
    kst_today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
    if not date_str and not force and d != kst_today:
        print(f"[중단] 오늘({kst_today}) 웹진이 아닙니다. (data 날짜: {d or '없음'})")
        print("  오늘 웹진 생성이 완료되지 않아 이메일 초안을 만들지 않습니다.")
        print("  (특정 날짜로 강제 실행하려면: python stibee_email.py YYYY-MM-DD)")
        sys.exit(1)

    subject = f"한국자동차환경협회 뉴스 모니터링 ({d})"
    email_html = build_email_html(data)

    if dry_run:
        out = os.path.join(ROOT, "email_preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(email_html)
        print(f"[dry-run] HTML만 생성: {out} (API 호출 안 함)")
        print(f"  제목: {subject}")
        print(f"  본문 길이: {len(email_html)}자")
        return

    # 실제 생성: 필수 값 확인
    missing = [k for k, v in {
        "STIBEE_API_KEY": API_KEY, "STIBEE_LIST_ID": LIST_ID,
        "STIBEE_SENDER_EMAIL": SENDER_EMAIL,
    }.items() if not v]
    if missing:
        print(f"[오류] 다음 환경변수가 없습니다: {', '.join(missing)}")
        print("  Secrets/.env 에 등록하세요. (구조만 보려면: python stibee_email.py --dry-run)")
        sys.exit(1)

    print(f"이메일 초안 생성: {subject}")
    email_id = create_email(subject)
    if not email_id:
        print("[오류] 이메일 생성 실패 (id 없음)")
        sys.exit(1)
    print(f"  이메일 틀 생성 완료 (id={email_id})")

    set_content(email_id, email_html)
    print(f"  본문 삽입 완료")
    print(f"\n완료: 스티비에 '작성 중' 초안 생성됨 (id={email_id})")
    print("  → 스티비에서 담당자가 검토·편집·발송하세요.")


if __name__ == "__main__":
    main()
