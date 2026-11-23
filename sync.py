import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright

BASE = "https://cauc.bysjy.com.cn"
OUT = Path("docs/events.json")
CATEGORIES = [("/module/careers", "career", "宣讲会"), ("/module/jobfairs?type=1", "jobfair", "双选会"), ("/module/onlines", "online", "校园招聘")]
DATE = re.compile(r"(20\d\d)[年./-](\d{1,2})[月./-](\d{1,2})日?\s*(?:[（(][^）)\n]{1,10}[）)])?\s*(\d{1,2})[:：](\d{2})")
LINKS = "Array.from(document.querySelectorAll('a[href]'),a=>[a.innerText.trim(),a.href])"

def detail_id(url, kind):
    p = urlsplit(url)
    if p.hostname != "cauc.bysjy.com.cn" or p.path.rstrip("/") != "/detail/" + kind:
        return ""
    ident = parse_qs(p.query).get("id", [""])[0]
    return ident if ident.isdigit() else ""

def discover(browser, listing, kind):
    page = browser.new_page()
    try:
        page.goto(BASE + listing, wait_until="domcontentloaded")
        page.wait_for_function("document.querySelector('h2:not(:empty),a[href*=\"/detail/\"]') && !document.body.innerText.includes('{{')", timeout=30000)
        links = {u:t for t,u in page.evaluate(LINKS) if detail_id(u, kind)}
        headings = page.locator("h2").all_text_contents()
        for title in dict.fromkeys(t.strip() for t in headings if t.strip()):
            if title in links.values():
                continue
            locator = page.get_by_role("heading", name=title, exact=True).first
            if not locator.count():
                continue
            original = page.url
            locator.click(timeout=4000)
            page.wait_for_timeout(650)
            target = page.context.pages[-1]
            target.wait_for_load_state("domcontentloaded")
            if detail_id(target.url, kind):
                links[target.url] = title
            if target != page:
                target.close()
            if page.url != original:
                page.go_back(wait_until="domcontentloaded")
                page.wait_for_selector("h2", timeout=15000)
        return links
    finally:
        page.close()

def read(browser, url, title, tag):
    page = browser.new_page()
    try:
        page.goto(url,wait_until="domcontentloaded")
        page.wait_for_function("!document.body.innerText.includes('{{') && document.body.innerText.includes('招聘')",timeout=30000)
        page.wait_for_timeout(1200)
        body=page.locator("body").inner_text()
        for line in ("已取消","宣讲取消","活动取消"):
            if line in body[:1800]:
                return None
        event=dict(id=urlsplit(url).path.split("/")[-1]+"-"+parse_qs(urlsplit(url).query)["id"][0],
                   title=title,company="",type=tag,date="",startTime="",endTime="",venue="",sourceUrl=url,url="")
        original_links={u for t,u in page.evaluate(LINKS) if re.fullmatch(r"(?:阅读|查看)?(?:公众号|微信)?原文",t) and re.match(r"https://mp\.weixin\.qq\.com/s(?:/|\?)",u)}
        if len(original_links)==1:
            event["url"]=original_links.pop()
        if tag=="校园招聘":
            return event
        stamp=re.search(r"(?:宣讲时间|举办时间|举办日期|双选会时间|会议时间|活动时间|召开时间)\s*[：:]\s*([^\n]+)",body)
        venue=re.search(r"(?:宣讲地点|举办地点|会议地点|双选会地点|活动地点|召开地点)\s*[：:]\s*([^\n]+)",body)
        match=DATE.search(stamp[1]) if stamp else None
        if not match or not venue:
            raise ValueError("缺少明确的活动时间或地点")
        start=datetime(*map(int,match.groups()))
        event.update(date=start.date().isoformat(),startTime=start.strftime("%H:%M"),
                     venue=re.split(r"点击人次|浏览次数|所在学校",venue[1])[0].strip())
        if not event["venue"] or "待定" in event["venue"]:
            raise ValueError("地点待定")
        return event
    finally:
        page.close()

def main():
    events={e["id"]:e for e in json.loads(OUT.read_text("utf8"))} if OUT.exists() else {}
    handled=0
    with sync_playwright() as client:
        browser=client.chromium.launch()
        for listing,kind,tag in CATEGORIES:
            try:
                links=discover(browser,listing,kind)
            except Exception as exc:
                print("栏目未读取",tag,str(exc)[:220])
                links={}
            if not links:
                home=browser.new_page()
                try:
                    home.goto(BASE,wait_until="domcontentloaded")
                    home.wait_for_timeout(1600)
                    links={u:t for t,u in home.evaluate(LINKS) if t and detail_id(u,kind)}
                finally:
                    home.close()
            for url,title in links.items():
                ident=kind+"-"+detail_id(url,kind)
                try:
                    record=read(browser,url,title,tag)
                except Exception as exc:
                    print("待核对",url,str(exc)[:200])
                    continue
                handled+=1
                if record:
                    events[ident]=record
                else:
                    events.pop(ident,None)
        browser.close()
    if not handled:
        raise SystemExit("未获取可核实数据，保留上次版本")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(events.values(),key=lambda e:(e["date"],e["startTime"],e["id"])),ensure_ascii=False,indent=2),encoding="utf8")

if __name__=="__main__":
    main()
