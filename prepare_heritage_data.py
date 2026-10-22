import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "index.html"
CSV_PATH = ROOT / "heritage_content.csv"
DETAIL_API = "https://www.khs.go.kr/cha/SearchKindOpenapiDt.do"
REGION_CODES = {
    "서울특별시": "11",
    "부산광역시": "21",
    "대구광역시": "22",
    "인천광역시": "23",
    "광주광역시": "24",
    "대전광역시": "25",
    "울산광역시": "26",
    "세종특별자치시": "45",
    "경기도": "31",
    "강원특별자치도": "32",
    "충청북도": "33",
    "충청남도": "34",
    "전북특별자치도": "35",
    "전라북도": "35",
    "전라남도": "36",
    "전남광주통합특별시": "36",
    "경상북도": "37",
    "경상남도": "38",
    "제주특별자치도": "50",
    "전국": "ZZ",
}
CODE_OVERRIDES = {
    36: ("11", "0000480000100", "32"),
    54: ("11", "0001010000000", "32"),
    73: ("11", "0002330000100", "38"),
    277: ("12", "0002200000100", "37"),
    827: ("12", "0023140000000", "36"),
    828: ("12", "0023150000000", "37"),
}
# These entries are present in the official list API, although the detail API returns blank items.
LIST_CONFIRMED = {
    193: ("12", "0001090000000", "36"),
    194: ("12", "0001100000000", "36"),
    228: ("12", "0001540000000", "36"),
    489: ("12", "0006000000000", "36"),
    598: ("12", "0011180000000", "36"),
    643: ("12", "0013950000000", "36"),
    827: ("12", "0023140000000", "36"),
    833: ("12", "0023270000000", "36"),
    834: ("12", "0023280000000", "36"),
}
CSV_FIELDS = (
    "id",
    "ccbaKdcd",
    "ccbaAsno",
    "ccbaCtcd",
    "content",
    "era",
    "quantity",
    "matched",
)
TAG_PATTERN = re.compile(r"<\s*/?\s*([A-Za-z_][\w.-]*)\b")
SUMMARY_PATH = ROOT / "summaries.json"
OBJECT_TYPES = (
    "당간지주",
    "탑비",
    "대성전",
    "대웅전",
    "석탑",
    "승탑",
    "석등",
    "불상",
    "마애불",
    "향교",
    "사당",
    "서원",
    "성곽",
    "성문",
    "다리",
    "교량",
    "불화",
    "석불",
    "전적",
    "도자기",
)
REVIEWED_SUMMARIES = {
    6: "성주사터의 탑비는 낭혜화상의 행적과 신라 골품제 연구자료다.",
    505: "청룡사 옛터의 비로, 보각국사를 기리려 조선 태조 때 세웠다.",
    279: "가야산 바위에 새긴 7.5m 불상으로, 9세기 무렵 작품으로 추정된다.",
    786: "영월 창절사는 충신 열 명을 기리려 1685년에 세운 사당이다.",
    754: "순천향교 대성전은 1649년 상량문이 남은 17세기 중엽 목조건축이다.",
    501: "영천향교 대성전은 공자 등 여러 성현의 위패를 모신다.",
    636: "육송정홍교는 고성군 경계에 놓인 길이 10.6m의 돌다리다.",
    170: "강릉의 당간지주는 절의 깃대를 세운 당간을 양쪽에서 받치는 돌기둥이다.",
    245: "망해사 북쪽의 통일신라 승탑 두 기 중 동쪽 탑은 1960년 복원됐다.",
    658: "통도사 삼층석탑은 신라 말 석탑으로, 두 단 기단 위에 3층 탑신을 올렸다.",
}


def load_map_data():
    source = INDEX_PATH.read_text(encoding="utf-8")
    match = re.search(r"const\s+RAW\s*=\s*(\[.*?\]);", source, re.S)
    if not match:
        raise RuntimeError("Could not locate the RAW map array in index.html")
    return json.loads(match.group(1))


def get_tag(xml, tag):
    match = re.search(
        rf"<\s*{re.escape(tag)}\b[^>]*>(.*?)<\s*/\s*{re.escape(tag)}\s*>",
        xml,
        re.S | re.I,
    )
    if not match:
        return ""
    value = match.group(1).strip()
    value = re.sub(r"^<!\[CDATA\[(.*?)\]\]>$", r"\1", value, flags=re.S)
    value = re.sub(r"</?[A-Za-z][^>]*>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return html.unescape(value.strip())


def make_request(record):
    raw = record["raw"]
    override = CODE_OVERRIDES.get(record["id"])
    if override:
        kind_code, designation, region_code = override
    else:
        kind_code = "11" if raw[0] == 0 else "12"
        region_code = REGION_CODES.get(raw[3])
        if region_code is None:
            raise ValueError(f"Unknown region: {raw[3]}")
        designation = f"{int(raw[2]):06d}0000000"
    params = {
        "ccbaKdcd": kind_code,
        "ccbaAsno": designation,
        "ccbaCtcd": region_code,
    }
    url = DETAIL_API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return request, kind_code, designation, region_code


def fetch_record(record):
    request, kind_code, designation, region_code = make_request(record)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                xml = response.read().decode("utf-8", errors="replace")
            actual = (
                get_tag(xml, "ccbaKdcd"),
                get_tag(xml, "ccbaAsno"),
                get_tag(xml, "ccbaCtcd"),
            )
            expected = (kind_code, designation, region_code)
            if actual != expected:
                raise ValueError(
                    f"API record mismatch for id {record['id']}: "
                    f"expected {expected}, got {actual}"
                )
            api_name = get_tag(xml, "ccbaMnm1")
            list_codes = LIST_CONFIRMED.get(record["id"])
            list_match = bool(list_codes and list_codes == expected)
            return {
                "id": record["id"],
                "ccbaKdcd": kind_code,
                "ccbaAsno": designation,
                "ccbaCtcd": region_code,
                "content": get_tag(xml, "content"),
                "era": get_tag(xml, "ccceName"),
                "quantity": get_tag(xml, "ccbaQuan"),
                "matched": bool(api_name) or list_match,
            }, xml
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            if attempt == 5:
                raise RuntimeError(
                    f"Failed id={record['id']} after 5 retries: {error}"
                ) from error
            time.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


def map_records(raw):
    return [
        {
            "id": index,
            "raw": row,
            "name": row[1],
            "kind": "국보" if row[0] == 0 else "보물",
        }
        for index, row in enumerate(raw)
    ]


def test_mode(records):
    xml_tags = []
    for record in records[:5]:
        data, xml = fetch_record(record)
        for tag in TAG_PATTERN.findall(xml):
            if tag not in xml_tags:
                xml_tags.append(tag)
        print(
            f"TEST {record['id']} {record['name']}: "
            f"matched={data['ccbaKdcd']}/{data['ccbaAsno']}/{data['ccbaCtcd']} "
            f"item_found={data['matched']} "
            f"era={data['era']!r} quantity={data['quantity']!r} "
            f"content={data['content'][:180]!r}"
        )
        time.sleep(0.3)
    print("XML tags:", ", ".join(xml_tags))


def write_all(records):
    existing = {}
    if CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as file:
            existing = {}
            for row in csv.DictReader(file):
                match_flag = row.get("matched", "")
                if match_flag:
                    row["matched"] = match_flag.lower() == "true"
                else:
                    row["matched"] = bool(
                        row.get("content") or row.get("era") or row.get("quantity")
                    )
                existing[int(row["id"])] = row

    matched = dict(existing)
    failed = []
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for index, record in enumerate(records, 1):
            if record["id"] in matched and matched[record["id"]]["matched"]:
                writer.writerow(matched[record["id"]])
                continue
            try:
                data, _ = fetch_record(record)
                matched[record["id"]] = data
                writer.writerow(data)
                file.flush()
            except (RuntimeError, ValueError) as error:
                failed.append((record["id"], str(error)))
                if record["id"] in matched:
                    writer.writerow(matched[record["id"]])
            if index % 50 == 0:
                print(f"Progress: {index}/{len(records)}; matched={len(matched)}")
            time.sleep(0.25)
    return matched, failed


def first_sentence(text):
    endings = ("다.", "음.", "함.", "됨.", "임.")
    stops = [text.find(ending) for ending in endings if text.find(ending) >= 0]
    if not stops:
        return text.strip()
    return text[: min(stops) + 2].strip()


def topic_particle(title):
    base_title = re.sub(r"\([^)]*\)$", "", title).rstrip()
    last = ord(base_title[-1]) - 0xAC00 if base_title else -1
    return "은" if 0 <= last <= 11171 and last % 28 else "는"


def summary_for(record, source):
    title = record["name"]
    if not source["content"]:
        raw = record["raw"]
        if raw[3] == "전국":
            place = title.split()[0]
        else:
            place = " ".join(part for part in (raw[3], raw[4]) if part)
        era = source["era"] or "시대 미상"
        return f"{era} · {record['kind']} · {place}", True

    sentence = first_sentence(source["content"])
    sentence = re.sub(r"\([^)]*\)", "", sentence)
    subject = f"{title}{topic_particle(title)}"
    sentence = sentence.replace("이것은 ", "")
    sentence = sentence.replace("이 유물은 ", "")
    sentence = sentence.replace("이 탑은 ", f"{subject} ")
    sentence = sentence.replace("이 비는 ", f"{subject} ")
    sentence = sentence.replace("이 석등은 ", f"{subject} ")
    sentence = sentence.replace("불렀다.", "불렸다.")
    sentence = sentence.replace("세웠다.", "건립했다.")
    sentence = sentence.replace("만들었다.", "지었다.")
    sentence = sentence.replace("세워진", "건립된")
    sentence = sentence.replace("남아 있는", "남은")
    sentence = sentence.replace("약 ", "")
    if len(sentence) <= 50:
        return sentence, False

    first_clause = sentence.split(",", 1)[0].strip()
    if first_clause.endswith("으로"):
        compact = first_clause[:-2] + "이다."
        if len(compact) <= 50:
            return compact, False
    elif first_clause.endswith("로"):
        compact = first_clause[:-1] + "이다."
        if len(compact) <= 50:
            return compact, False

    content = source["content"]
    if title.endswith("루"):
        object_type = "누각"
    else:
        object_type = next(
            (kind for kind in OBJECT_TYPES if kind in title or kind in sentence), None
        )
        if object_type is None:
            object_type = next(
                (kind for kind in OBJECT_TYPES if kind in content), None
            )
    object_type = object_type or "문화유산"
    era = source["era"]
    period = next(
        (
            label
            for word, label in (
                ("통일신라", "통일신라시대"),
                ("발해", "발해시대"),
                ("고구려", "고구려시대"),
                ("백제", "백제시대"),
                ("신라", "신라시대"),
                ("고려", "고려시대"),
                ("조선", "조선시대"),
                ("삼국", "삼국시대"),
            )
            if word in era
        ),
        "옛 시대",
    )
    topic = topic_particle(title)
    if object_type in title:
        object_type = "문화유산"
    compact = f"{title}{topic} {period}의 {object_type}이다."
    if len(compact) <= 50:
        return compact, False
    return f"{period}의 {object_type}으로 전해진다.", False


def write_summaries(records, matched):
    summaries = {}
    by_id = {record["id"]: record for record in records}
    for record_id, record in matched.items():
        summary, fallback = summary_for(by_id[record_id], record)
        if record_id in REVIEWED_SUMMARIES:
            summary = REVIEWED_SUMMARIES[record_id]
            fallback = False
        summaries[str(record_id)] = {"s": summary, "fallback": fallback}
    SUMMARY_PATH.write_text(
        json.dumps(summaries, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {SUMMARY_PATH.name}: {len(summaries)} entries")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--summaries-only", action="store_true")
    args = parser.parse_args()
    records = map_records(load_map_data())
    print(f"Map records: {len(records)}")
    if args.test_mode:
        test_mode(records)
    elif args.full:
        matched, failed = write_all(records)
        empty = sum(not row["content"] for row in matched.values())
        unmatched = sum(not row["matched"] for row in matched.values())
        print(
            f"collected_success={sum(bool(row['content']) for row in matched.values())} "
            f"content_empty={empty} matching_failures={unmatched + len(failed)} "
            f"records_saved={len(matched)} request_failures={len(failed)}"
        )
        if failed:
            print("Failures:", json.dumps(failed, ensure_ascii=False))
        write_summaries(records, matched)
    elif args.summaries_only:
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as file:
            matched = {
                int(row["id"]): row for row in csv.DictReader(file)
            }
        write_summaries(records, matched)
    else:
        parser.error("choose --test-mode, --full, or --summaries-only")


if __name__ == "__main__":
    main()
