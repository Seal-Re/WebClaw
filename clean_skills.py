from pathlib import Path
import re


def main() -> None:
    path = Path("skill_list.txt")
    if not path.exists():
        raise SystemExit("skill_list.txt not found in current directory")

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    META_TAGS = {"macOS", "Linux", "Windows", "Highlighted"}

    def is_next_name(s: str) -> bool:
        s = s.strip()
        if not s:
            return False
        if s.startswith("/"):
            return False
        if s == "by":
            return False
        if s in META_TAGS:
            return False
        if s.startswith("@"):
            return False
        if s.startswith("★"):
            return False
        # 过滤 86.2k、12 v、4 v 之类的统计信息
        if re.match(r"^[0-9]", s):
            return False
        return True

    records = []
    n = len(lines)
    i = 0

    while i < n:
        name = lines[i].strip()
        if not name:
            i += 1
            continue

        # 要求下一行是以 / 开头的 slug，否则当作噪音跳过
        if i + 1 >= n or not lines[i + 1].strip().startswith("/"):
            i += 1
            continue

        slug = lines[i + 1].strip()

        # 收集描述：从 slug 后面开始，一直到遇到 "by"
        j = i + 2
        desc_parts = []
        while j < n and lines[j].strip() != "by":
            t = lines[j].strip()
            if t and t not in META_TAGS:
                desc_parts.append(t)
            j += 1

        description = " ".join(desc_parts)
        records.append((name, slug, description))

        # 跳过 "by" 以及作者/统计信息，找到下一条记录的名字行
        if j < n and lines[j].strip() == "by":
            j += 1

        k = j
        while k < n and not is_next_name(lines[k]):
            k += 1

        i = k

    out_path = Path("skill_list_clean.tsv")
    with out_path.open("w", encoding="utf-8") as f:
        for name, slug, desc in records:
            f.write(f"{name}\t{slug}\t{desc}\n")

    print(f"wrote {len(records)} rows to {out_path}")


if __name__ == "__main__":
    main()

