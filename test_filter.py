POLLABLE_TYPES = frozenset({"analogInput", "analogOutput", "device"})
# Simulate what BAC0 might return
import bacpypes3.bap
from bacpypes3.primitivedata import ObjectType

items = [
    ObjectType("analogInput"),
    ObjectType("file"),
    ObjectType(10), # 10 is file
    "file",
    "analogInput",
    (ObjectType("file"), 1),
    ("file", 1)
]

print("Allowed types (lower):", {t.lower() for t in POLLABLE_TYPES})
for item in items:
    # Logic from bacnet_service.py
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        ot = str(item[0])
    else:
        ot = getattr(item, 'objectType', None) or str(item[0]) if hasattr(item, '__getitem__') else str(item)
    
    passed = ot.lower() in {t.lower() for t in POLLABLE_TYPES}
    print(f"Item: {repr(item):<25} -> parsed ot: {repr(ot):<20} -> passed: {passed}")
