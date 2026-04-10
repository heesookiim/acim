import json

with open("version/park/splits/교과서_박영수_001.json") as f:
    data = json.load(f)

def sort_key(item):
    p = item.get("pageNumber", 0)
    bbox = item.get("bbox", [0,0,0,0])
    y = bbox[1] if bbox and len(bbox)>=2 else 0
    x = bbox[0] if bbox and len(bbox)>=1 else 0
    return (p, y, x)

data["contents"].sort(key=sort_key)

for item in data["contents"]:
    if item.get("pageNumber") == 29:
        bbox = item.get("bbox", [0,0,0,0])
        print(f"y={bbox[1]:3d} type={item['type']:10s} text={item['content'][:30]}")
