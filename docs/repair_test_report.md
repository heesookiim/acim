# Parse Data Repair Test Report

## Status

`parse_data.json` was not modified. The generated comparison and repaired artifacts are:

- `parse_data.local.json`: local PyMuPDF extraction only.
- `parse_data.repaired.json`: original structure plus cleaned local PDF text.

The repaired file is suitable for review before promoting it over `parse_data.json`.

## Tests Performed

- JSON/schema sanity:
  - `parse_data.json`, `parse_data.local.json`, and `parse_data.repaired.json` all load as valid JSON.
  - No empty documents.
  - No duplicate or unsorted page numbers.
  - Required page keys and content item keys are present.
- Defect audit:
  - Original baseline contains hidden Unicode and garbling.
  - Local extraction and repaired output are clean under the high-confidence audit.
- Known bad fragment scan:
  - Confirmed known bad fragments exist in `parse_data.json`.
  - Confirmed those fragments are gone from `parse_data.local.json` and `parse_data.repaired.json`.
- Search compatibility using repaired data:
  - `존재`: 250 original textbook matches.
  - `특별`: 89 original textbook matches; 90 Park textbook matches.
  - `하느님과 다르게 사랑하려`: 2 original textbook matches.
  - `용서`: 136 original workbook matches; 161 Park workbook matches.
- Reader API compatibility using repaired data:
  - Chapter 1 / `기적의 원리`: 200 OK.
  - Chapter 7 / `흥정 대(對) 치유`: 200 OK.
  - Chapter 15 / `거룩한 순간과 특별한 관계`: 200 OK.
  - Chapter 31 / `구원자의 비전`: 200 OK.

## Audit Summary

Before repair, `parse_data.json` had:

- `U+00A0 NO-BREAK SPACE`: 9,554 total occurrences.
- `U+00AD SOFT HYPHEN`: 5 occurrences.
- Known garbled fragments:
  - `3}5|7]017}`: 1
  - `7700.40`: 1
  - `740172721722`: 1
  - `o k _ { 2 } = 1`: 1
  - `^ { o \bar { } }`: 17
- Known spacing defects:
  - `특 별`: 10
  - `못하시 는`: 1

After repair, `parse_data.repaired.json` has:

- `U+00A0 NO-BREAK SPACE`: 0
- `U+00AD SOFT HYPHEN`: 0
- All listed known garbled fragments: 0
- `특 별`: 0
- `못하시 는`: 0

## Before / After Corrections

| Location | Before | After |
|---|---|---|
| `original / 교과서 / page 129` | `무시간적인 것은 증 7 3}5|7]017} 3 .7073 지각한다면` | `무시간적인 것은 증가하도록 영원히 창조되었기에, 증가한다고 해서 변하지 않는다. 그것이 증가하지 않는다고 지각한다면` |
| `original / 교과서 / page 129` | `너는 또한 누가 그것을 창조하셨는지, 혹은 7700.40 . 코 감춰진 적이 없기 때문이다.` | `너는 또한 누가 그것을 창조하셨는지, 혹은 그가 누구이신지도 모르는 것이다. 하느님은 너에게 이것을 드러내지 않으신다. 그것은 결코 감춰진 적이 없기 때문이다.` |
| `original / 교과서 / page 310` | `네가 어떻게 결정할 수 있겠는가? 740172721722` | `네가 어떻게 결정할 수 있겠는가? 과거는 너에게 그렇게 가르쳤다. 하지만 거룩한 순간은 그렇지 않다고 가르친다.` |
| `original / 교과서 / page 21` | `특별성은 배제가o k _ { 2 } = 1 포함함에서 생겨난다.` | `특별성은 배제가 아닌 포함함에서 생겨난다.` |
| `original / 교과서 / page 52` | `내가 처벌받은 것은 네가 나빠서가^ { o \bar { } } 44.37 42 3773 비로운 레슨이 상실된다.` | `내가 처벌받은 것은 네가 나빠서가 아니다. 속죄가 어떤 식으로든 이런 종류의 왜곡으로 오염된다면, 속죄가 가르치는 지극히 자비로운 레슨이 상실된다.` |
| `original / 교과서 / page 337` | `특 별한 관계` | `특별한 관계` |
| `original / 교과서 / page 310` | `특별한 사랑을 알지 못하시 는 하느님과 다르게 사랑하려 한다면` | `특별한 사랑을 알지 못하시는 하느님과 다르게 사랑하려 한다면` |
| `original / 학생용 연습서 / page 90` | `어떤 특 별한 환경도 필요로 하지 않도록` | `어떤 특별한 환경도 필요로 하지 않도록` |
| `original / 학생용 연습서 / page 416` | `이 특 별한 생각들을 매일 복습하되` | `이 특별한 생각들을 매일 복습하되` |
| `park / 교과서 / page 498` | `그대의 특 별함을 그대의 특별한 기능으로 본다` | `그대의 특별함을 그대의 특별한 기능으로 본다` |

## Remaining Notes Before Promotion

- `parse_data.repaired.json` preserves the original page list. Therefore `original / 학생용 연습서` still has 579 page entries while its `pageCount` is 580. This mismatch already existed in `parse_data.json`; the repair did not introduce it.
- `parse_data.local.json` has 580 local pages for `original / 학생용 연습서`, so the missing baseline page can be investigated separately if desired.
- Python generated or modified `__pycache__` files during validation; these are unrelated to the repair data.

## Recommendation

Review `parse_data.repaired.json` and this report. If the remaining page-count mismatch is acceptable for the current deployment, promote `parse_data.repaired.json` to `parse_data.json` with a backup of the current file.
