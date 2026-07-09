"""
====================================================================
한국자동차환경협회 뉴스 웹진 - 뉴스 수집 스크립트 (STEP 1)
====================================================================
역할: 네이버 검색 API로 뉴스를 가져와서,
      전날 발행분 + 카테고리별 2차 필터를 거쳐 JSON으로 저장한다.

수집 방식 (협회 기준표 반영):
  [일반 카테고리 - 해석 A]
    primary(주요 키워드)로 네이버 검색
    → 그 결과 중 general(일반 키워드)이 제목/요약에 있는 기사만 남김
    → general이 비어있으면 필터 없이 전부 통과 (협회/기타/해외)

  [회원사 카테고리]
    회사명 54개로 각각 검색
    → 결과 중 MEMBER_CONTEXT(자동차·충전·배출 등)가 있는 기사만 남김
    → 무관한 동명이의 뉴스 제거

공통:
  - 전날 발행분만 남김 (KST 기준)
  - HTML 태그·특수문자 정제
  - 중복 제거(제목/URL)
  - 부정 뉴스 필터링은 STEP 2(filter.py). 여기선 안 함.

API 키:
  - 로컬: .env 의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
  - 자동: GitHub Secrets 의 동일 이름

라이브러리:
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from keywords import (
    KEYWORDS, GLOBAL_KEYWORDS,
    MEMBER_COMPANIES, MEMBER_CONTEXT,
)

# --------------------------------------------------------------------
# API 키
# --------------------------------------------------------------------
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("[오류] 네이버 API 키가 없습니다.")
    print("  로컬: .env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET")
    print("  자동: GitHub Secrets 에 동일 이름 등록")
    raise SystemExit(1)

# --------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
DISPLAY = 100
SORT = "date"
REQUEST_DELAY = 0.1
MAX_PER_CATEGORY = 8

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY_KST = datetime.datetime.now(KST).date()
TARGET_DATE = TODAY_KST - datetime.timedelta(days=1)   # 전날 발행분만


def clean_text(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    return text.strip()


def get_pub_date(pubdate_str):
    try:
        dt = datetime.datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).date()
    except Exception:
        return None


def pub_date_label(d):
    return f"{d.month}.{d.day}" if d else ""


def raw_search(query):
    """네이버 뉴스 API 호출 → '전날 발행' 정제 기사 리스트 (2차 필터 전)."""
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {"query": query, "display": DISPLAY, "sort": SORT}
    try:
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"    [요청 실패] '{query}': {e}")
        return []
    if resp.status_code != 200:
        print(f"    [응답 오류] '{query}': HTTP {resp.status_code}")
        return []

    out = []
    for it in resp.json().get("items", []):
        pub = get_pub_date(it.get("pubDate", ""))
        if pub != TARGET_DATE:          # 전날 발행분만
            continue
        title = clean_text(it.get("title", ""))
        summary = clean_text(it.get("description", ""))
        if not title:
            continue
        out.append({
            "title": title,
            "source": "",
            "date": pub_date_label(pub),
            "summary": summary,
            "url": it.get("originallink") or it.get("link", ""),
        })
    return out


def passes_filter(article, filter_keywords):
    """기사의 제목+요약에 filter_keywords 중 하나라도 있으면 True.
    filter_keywords가 비어있으면 무조건 True(필터 없음)."""
    if not filter_keywords:
        return True
    haystack = article["title"] + " " + article["summary"]
    return any(kw in haystack for kw in filter_keywords)


def collect_standard(cat_dict):
    """일반 카테고리 수집 (해석 A: primary 검색 → general 필터)."""
    result = {}
    for cat_name, conf in cat_dict.items():
        primary = conf.get("primary", [])
        general = conf.get("general", [])
        seen_titles, seen_urls = set(), set()
        articles = []

        for kw in primary:                      # 주요 키워드로 검색
            for art in raw_search(kw):
                # 2차 필터: 일반 키워드가 든 기사만 (general 비면 전부 통과)
                if not passes_filter(art, general):
                    continue
                if art["title"] in seen_titles or (art["url"] and art["url"] in seen_urls):
                    continue
                seen_titles.add(art["title"])
                if art["url"]:
                    seen_urls.add(art["url"])
                articles.append(art)
            time.sleep(REQUEST_DELAY)

        result[cat_name] = articles[:MAX_PER_CATEGORY]
        print(f"  [{cat_name}] {len(result[cat_name])}건")
    return result


def collect_members():
    """회원사 수집 (회사명 검색 → 맥락 키워드 필터)."""
    seen_titles, seen_urls = set(), set()
    articles = []

    for company in MEMBER_COMPANIES:            # 회사명으로 검색
        for art in raw_search(company):
            # 맥락 키워드(자동차·충전·배출 등)가 있어야 관련 기사로 인정
            if not passes_filter(art, MEMBER_CONTEXT):
                continue
            if art["title"] in seen_titles or (art["url"] and art["url"] in seen_urls):
                continue
            seen_titles.add(art["title"])
            if art["url"]:
                seen_urls.add(art["url"])
            # 어느 회원사로 잡혔는지 표시(선택) - 요약 앞에 회사명 참고용은 넣지 않음
            articles.append(art)
        time.sleep(REQUEST_DELAY)

    articles = articles[:MAX_PER_CATEGORY]
    print(f"  [회원사 뉴스] {len(articles)}건")
    return {"회원사 뉴스": articles}


def main():
    print("뉴스 수집 시작")
    print(f"  실행일(KST): {TODAY_KST}")
    print(f"  수집 대상 발행일: {TARGET_DATE} (전날 하루)")
    print("=" * 50)

    print("[국내 뉴스 - 일반 카테고리]")
    daily = collect_standard(KEYWORDS)

    print("[회원사 뉴스]")
    member = collect_members()
    daily.update(member)     # 회원사 뉴스를 daily에 합침

    print("\n[해외 뉴스]")
    global_news = collect_standard(GLOBAL_KEYWORDS)

    # STEP 2 부정 필터링 자리 (지금 없음)

    date_str = TODAY_KST.strftime("%Y-%m-%d")
    dow_kr = ["월", "화", "수", "목", "금", "토", "일"][TODAY_KST.weekday()]

    data = {
        "no": TODAY_KST.strftime("%m%d"),
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


if __name__ == "__main__":
    main()
