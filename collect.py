"""
====================================================================
한국자동차환경협회 뉴스 웹진 - 뉴스 수집 스크립트 (STEP 1)
====================================================================
역할: 네이버 검색 API로 키워드별 뉴스를 가져와서,
      전날 발행분만 남기고 → 정제 → 중복 제거 → 카테고리 분류 후
      JSON으로 저장한다. 이 JSON을 STEP 3(generate.py)이 읽어 웹진을 만든다.

이번 개정 내용:
  - 뉴스 발행일 필터 추가: "실행 전날 하루"에 발행된 기사만 남긴다.
    (예: 7월 7일 실행 → 7월 6일 발행 기사만)
    한국시간(KST) 기준으로 판정하므로, 자동 실행(UTC 서버)에서도 날짜가 안 밀린다.

주의:
  - 부정 뉴스 필터링(리콜/사고 제외)은 아직 넣지 않는다. (STEP 2에서 추가 예정)
    아래 main() 안에 넣을 자리를 주석으로 표시해 두었다.
  - API 키는 코드에 직접 쓰지 않는다.
      · 로컬 실행: 같은 폴더의 .env 파일에서 읽는다.
      · 자동 실행(GitHub Actions): Secrets에 등록한 값을 환경변수로 읽는다.
    두 경우 모두 아래 환경변수 이름이 같아야 한다:
      NAVER_CLIENT_ID / NAVER_CLIENT_SECRET

로컬 준비물:
  - 같은 폴더에 .env 파일을 만들고 아래 두 줄 (키는 본인 것):
      NAVER_CLIENT_ID=발급받은_아이디
      NAVER_CLIENT_SECRET=발급받은_시크릿
  - .env 는 절대 GitHub에 올리지 않는다 (.gitignore 처리).

라이브러리 설치 (터미널에서 한 번):
  pip install requests python-dotenv

사용법:
  python collect.py
====================================================================
"""
import os
import re
import json
import html
import time
import datetime
import requests

# python-dotenv 는 로컬 .env 를 읽기 위한 것.
# 자동 실행(GitHub Actions) 환경엔 .env 가 없어도 되도록, 없으면 조용히 넘어간다.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from keywords import KEYWORDS, GLOBAL_KEYWORDS

# --------------------------------------------------------------------
# 1) 환경변수에서 API 키 읽기 (.env 또는 GitHub Secrets)
# --------------------------------------------------------------------
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("[오류] 네이버 API 키가 없습니다.")
    print("  로컬 실행: 같은 폴더의 .env 파일에 아래 두 줄을 넣어주세요.")
    print("    NAVER_CLIENT_ID=발급받은_아이디")
    print("    NAVER_CLIENT_SECRET=발급받은_시크릿")
    print("  자동 실행: GitHub Secrets에 같은 이름으로 등록되어 있어야 합니다.")
    raise SystemExit(1)

# --------------------------------------------------------------------
# 경로 및 설정
# --------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
DISPLAY = 100         # 키워드당 가져올 기사 수 (최대 100). 전날 필터로 걸러지므로 넉넉히.
SORT = "date"         # date=최신순 (전날 기사를 놓치지 않으려면 최신순이 유리)
REQUEST_DELAY = 0.1   # 요청 사이 간격(초)
MAX_PER_CATEGORY = 8  # 카테고리당 최종 최대 기사 수

# 한국시간(KST) 기준
KST = datetime.timezone(datetime.timedelta(hours=9))

# "전날" 날짜를 실행 시점 기준으로 한 번 계산해 둔다 (KST 기준)
TODAY_KST = datetime.datetime.now(KST).date()
TARGET_DATE = TODAY_KST - datetime.timedelta(days=1)   # 이 날짜 발행분만 남긴다


def clean_text(raw):
    """네이버가 주는 제목/요약의 HTML 태그와 특수문자를 정제한다."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)      # <b> 같은 태그 제거
    text = html.unescape(text)              # &quot; &amp; 등 → 실제 문자
    return text.strip()


def get_pub_date(pubdate_str):
    """네이버 pubDate(예: 'Mon, 06 Jul 2026 15:30:00 +0900')를 KST 기준 date로 변환.
    실패하면 None."""
    try:
        dt = datetime.datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).date()
    except Exception:
        return None


def pub_date_label(d):
    """date 객체를 'M.D' 형식 문자열로."""
    return f"{d.month}.{d.day}" if d else ""


def search_news(query):
    """네이버 뉴스 API에 키워드 하나로 요청 → '전날 발행' 기사만 정제해서 반환."""
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": DISPLAY, "sort": SORT}

    try:
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"  [요청 실패] '{query}': {e}")
        return []

    if resp.status_code != 200:
        print(f"  [응답 오류] '{query}': HTTP {resp.status_code}")
        return []

    items = resp.json().get("items", [])
    results = []
    for it in items:
        pub = get_pub_date(it.get("pubDate", ""))

        # ★ 전날 발행 필터: 발행일이 전날(TARGET_DATE)이 아니면 건너뜀
        if pub != TARGET_DATE:
            continue

        title = clean_text(it.get("title", ""))
        summary = clean_text(it.get("description", ""))
        url = it.get("originallink") or it.get("link", "")

        if not title:
            continue

        results.append({
            "title": title,
            "source": "",          # 네이버 뉴스 API는 언론사명을 직접 주지 않음
            "date": pub_date_label(pub),
            "summary": summary,
            "url": url,
        })
    return results


def collect_category(cat_dict):
    """카테고리별 키워드로 수집 + 중복 제거(제목/URL 기준)."""
    result = {}
    for cat_name, kw_list in cat_dict.items():
        seen_titles = set()
        seen_urls = set()
        articles = []
        for kw in kw_list:
            for art in search_news(kw):
                if art["title"] in seen_titles or (art["url"] and art["url"] in seen_urls):
                    continue
                seen_titles.add(art["title"])
                if art["url"]:
                    seen_urls.add(art["url"])
                articles.append(art)
            time.sleep(REQUEST_DELAY)
        result[cat_name] = articles[:MAX_PER_CATEGORY]
        print(f"  [{cat_name}] {len(result[cat_name])}건 (전날 발행분)")
    return result


def main():
    print(f"뉴스 수집 시작")
    print(f"  실행일(KST): {TODAY_KST}")
    print(f"  수집 대상 발행일: {TARGET_DATE} (전날 하루)")
    print("=" * 50)

    print("[국내 뉴스 수집]")
    daily = collect_category(KEYWORDS)

    print("\n[해외 뉴스 수집]")
    global_news = collect_category(GLOBAL_KEYWORDS)

    # ------------------------------------------------------------------
    # ※ STEP 2 부정 뉴스 필터링이 들어갈 자리 (지금은 비어 있음)
    #   나중에 여기서 daily, global_news 를 필터링 함수에 통과시킨다.
    #   예: daily = filter_negative(daily)
    #       global_news = filter_negative(global_news)
    # ------------------------------------------------------------------

    # STEP 3(generate.py)이 읽는 형식으로 저장.
    # 파일명은 '오늘'(실행일) 날짜. 내용은 전날 발행 기사.
    date_str = TODAY_KST.strftime("%Y-%m-%d")
    dow_kr = ["월", "화", "수", "목", "금", "토", "일"][TODAY_KST.weekday()]

    data = {
        "no": TODAY_KST.strftime("%m%d"),   # 임시 호수(월일). 실제 호수 규칙 확정 후 교체
        "date": date_str,
        "dow": dow_kr,
        "summary": "",
        "daily": daily,
        "global": global_news,
    }

    out_path = os.path.join(DATA_DIR, f"{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in daily.values()) + sum(len(v) for v in global_news.values())
    print("\n" + "=" * 50)
    print(f"완료: data/{date_str}.json 저장 (전날 발행 총 {total}건)")
    print("이제 generate.py 를 실행하면 이 데이터로 웹진이 만들어집니다.")


if __name__ == "__main__":
    main()
