import sys
def h(s):
    x = 5381
    for ch in s:
        x = ((x * 33) ^ ord(ch)) & 0xFFFFFFFF
    return x
for p in sys.argv[1:]:
    s = open(p, encoding="utf-8").read().rstrip("\n")
    print(p, len(s), h(s))
