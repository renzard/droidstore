"""compat/atl-compat.jar (android.app.TaskStackBuilder for ATL builds that lack it) is a valid dex and does its job.

No ART is available in the tests, so this file does two things on the REAL bytes of the committed jar:
  1) parses the dex strictly (checksum, SHA-1, section order, sorting rules, map list, code items);
  2) runs the methods in a small Dalvik interpreter (only the opcodes the class uses) with fake Android classes.

    python3 tests/test_compat_dex.py
"""
import hashlib
import os
import struct
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(ROOT, "compat", "atl-compat.jar")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_compat_dex as gen  # noqa: E402

# ---------------------------------------------------------------- the committed jar is what the generator writes
assert open(JAR, "rb").read() == gen.build_jar(), "compat/atl-compat.jar is out of date: run tools/make_compat_dex.py"
with zipfile.ZipFile(JAR) as z:
    assert z.namelist() == ["classes.dex"], z.namelist()
    assert z.getinfo("classes.dex").compress_type == zipfile.ZIP_STORED
    dex = z.read("classes.dex")


# ---------------------------------------------------------------- 1) strict parse
def u32(off):
    return struct.unpack_from("<I", dex, off)[0]


def u16(off):
    return struct.unpack_from("<H", dex, off)[0]


def uleb_at(off):
    value, shift = 0, 0
    while True:
        b = dex[off]
        off += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, off


assert dex[:8] == b"dex\n035\0"
assert u32(8) == zlib.adler32(dex[12:]) & 0xFFFFFFFF, "adler32"
assert dex[12:32] == hashlib.sha1(dex[32:]).digest(), "sha1"
assert u32(32) == len(dex), "file_size"
assert u32(36) == 0x70 and u32(40) == 0x12345678
assert u32(44) == 0 and u32(48) == 0, "no link section"
map_off = u32(52)
n_str, str_off = u32(56), u32(60)
n_type, type_off = u32(64), u32(68)
n_proto, proto_off = u32(72), u32(76)
n_field, field_off = u32(80), u32(84)
n_method, method_off = u32(88), u32(92)
n_class, class_off = u32(96), u32(100)
data_size, data_off = u32(104), u32(108)
assert str_off == 0x70 and type_off == str_off + 4 * n_str and proto_off == type_off + 4 * n_type
assert field_off == proto_off + 12 * n_proto and method_off == field_off + 8 * n_field
assert class_off == method_off + 8 * n_method and n_class == 1 and data_off == class_off + 32
assert data_off + data_size == len(dex) and data_size % 4 == 0

strings = []
for i in range(n_str):
    off = u32(str_off + 4 * i)
    assert data_off <= off < len(dex)
    size, p = uleb_at(off)
    end = dex.index(b"\0", p)
    assert end - p == size, "ASCII only: utf16 size == byte size"
    strings.append(dex[p:end].decode("ascii"))
assert strings == sorted(strings) and len(set(strings)) == n_str, "string_ids sorted, unique"

types = [strings[u32(type_off + 4 * i)] for i in range(n_type)]
assert [u32(type_off + 4 * i) for i in range(n_type)] == sorted(u32(type_off + 4 * i) for i in range(n_type))
assert len(set(types)) == n_type

protos = []
for i in range(n_proto):
    shorty, ret, params_off = struct.unpack_from("<III", dex, proto_off + 12 * i)
    params = []
    if params_off:
        assert params_off % 4 == 0
        count = u32(params_off)
        params = [types[u16(params_off + 4 + 2 * k)] for k in range(count)]
    want = ("L" if types[ret][0] in "L[" else types[ret]) + "".join("L" if p[0] in "L[" else p for p in params)
    assert strings[shorty] == want, (strings[shorty], want)
    protos.append((types[ret], tuple(params)))
keys = [(struct.unpack_from("<I", dex, proto_off + 12 * i + 4)[0], protos[i][1]) for i in range(n_proto)]
assert len(set(protos)) == n_proto

fields = []
for i in range(n_field):
    cls, typ, name = struct.unpack_from("<HHI", dex, field_off + 8 * i)
    fields.append((types[cls], strings[name], types[typ]))
    assert i == 0 or (cls, name, typ) > prev_field
    prev_field = (cls, name, typ)

methods = []
for i in range(n_method):
    cls, proto, name = struct.unpack_from("<HHI", dex, method_off + 8 * i)
    methods.append((types[cls], strings[name], protos[proto]))
    assert i == 0 or (cls, name, proto) > prev_method
    prev_method = (cls, name, proto)

cls_idx, flags, super_idx, ifaces, src, annos, class_data, static_vals = struct.unpack_from("<8I", dex, class_off)
assert types[cls_idx] == "Landroid/app/TaskStackBuilder;" and types[super_idx] == "Ljava/lang/Object;"
assert flags == 1 and ifaces == 0 and src == 0xFFFFFFFF and annos == 0 and static_vals == 0

# class data
p = class_data
n_static, p = uleb_at(p)
n_inst, p = uleb_at(p)
n_direct, p = uleb_at(p)
n_virtual, p = uleb_at(p)
assert n_static == 0 and n_inst == 2
inst_fields, prev = [], 0
for _ in range(n_inst):
    diff, p = uleb_at(p)
    acc, p = uleb_at(p)
    prev += diff
    inst_fields.append(fields[prev])
    assert acc == 2
assert sorted(f[1] for f in inst_fields) == ["mIntents", "mSourceContext"]
defined = {}
for group, n in (("direct", n_direct), ("virtual", n_virtual)):
    prev = 0
    for _ in range(n):
        diff, p = uleb_at(p)
        acc, p = uleb_at(p)
        code_off, p = uleb_at(p)
        prev += diff
        assert methods[prev][0] == "Landroid/app/TaskStackBuilder;"
        assert code_off % 4 == 0 and code_off >= data_off
        if group == "direct":
            assert acc & 0x8 or (acc & 0x10000 and methods[prev][1] == "<init>")
        else:
            assert not acc & (0x8 | 0x2 | 0x10000) and acc & 1
        defined[methods[prev]] = (acc, code_off)
assert n_direct + n_virtual == len(gen.METHODS) == len(defined)

# map list
n_items = u32(map_off)
assert map_off % 4 == 0
last, kinds = -1, []
for i in range(n_items):
    kind, _unused, count, off = struct.unpack_from("<HHII", dex, map_off + 4 + 12 * i)
    assert off > last or (kind == 0 and off == 0), "map list sorted by offset"
    last = off
    kinds.append(kind)
assert len(set(kinds)) == len(kinds) and kinds[0] == 0 and kinds[-1] == 0x1000
assert map_off + 4 + 12 * n_items == len(dex)
print("test_compat_dex (strict parse of %d bytes): OK" % len(dex))


# ---------------------------------------------------------------- 2) a small Dalvik interpreter
def code_of(name, params, ret=None):
    for key, (acc, off) in defined.items():
        if key[1] == name and key[2][1] == tuple(params) and (ret is None or key[2][0] == ret):
            regs, ins, outs, tries, dbg, size = struct.unpack_from("<HHHHII", dex, off)
            assert tries == 0 and dbg == 0
            units = list(struct.unpack_from("<%dH" % size, dex, off + 16))
            return acc, regs, ins, outs, units
    raise KeyError(name)


class Obj:
    def __init__(self, cls):
        self.cls, self.f = cls, {}


class FakeList(list):
    pass


TSB = "Landroid/app/TaskStackBuilder;"
calls = []


def native(cls, name, ret, params, args):
    """Fake framework: ArrayList, Object, Context.startActivity, PendingIntent.getActivities."""
    if (cls, name) == ("Ljava/lang/Object;", "<init>"):
        return None
    if (cls, name) == ("Ljava/util/ArrayList;", "<init>"):
        args[0].list = FakeList()
        return None
    if (cls, name) == ("Ljava/util/ArrayList;", "add"):
        args[0].list.append(args[1])
        return 1
    if (cls, name) == ("Ljava/util/ArrayList;", "get"):
        return args[0].list[args[1]]
    if (cls, name) == ("Ljava/util/ArrayList;", "size"):
        return len(args[0].list)
    if (cls, name) == ("Ljava/util/ArrayList;", "toArray"):
        target = args[1]
        assert isinstance(target, list) and len(target) == len(args[0].list), "array sized to the list"
        target[:] = args[0].list
        return target
    if (cls, name) == ("Landroid/content/Context;", "startActivity"):
        calls.append(("startActivity", args[1], args[2] or None))  # Dalvik: null is the register value 0
        return None
    if (cls, name) == ("Landroid/app/PendingIntent;", "getActivities"):
        calls.append(("getActivities", args[0], args[1], list(args[2]), args[3], args[4] or None))
        return "PENDING"
    raise AssertionError("unexpected framework call %s.%s" % (cls, name))


def invoke(method_key_idx_or_tuple, args):
    cls, name, (ret, params) = method_key_idx_or_tuple
    if cls == TSB:
        key = (cls, name, (ret, params))
        acc, regs, ins, outs, units = code_of(name, params, ret)
        return run(acc, regs, ins, units, args)
    return native(cls, name, ret, params, args)


def run(acc, regs, ins, units, args):
    assert len(args) == ins, (len(args), ins)
    v = [None] * regs
    v[regs - ins:] = args
    pc = 0
    result = None
    steps = 0
    while True:
        steps += 1
        assert steps < 1000, "runaway"
        u = units[pc]
        op = u & 0xFF
        if op == 0x0E:  # return-void
            return None
        if op in (0x0F, 0x11):  # return, return-object
            return v[u >> 8]
        if op in (0x0A, 0x0C):  # move-result(-object)
            v[u >> 8] = result
            pc += 1
        elif op == 0x12:  # const/4
            v[(u >> 8) & 0xF] = u >> 12 if u >> 12 < 8 else (u >> 12) - 16
            pc += 1
        elif op == 0x21:  # array-length
            v[(u >> 8) & 0xF] = len(v[u >> 12])
            pc += 1
        elif op == 0x22:  # new-instance
            v[u >> 8] = Obj(types[units[pc + 1]])
            pc += 2
        elif op == 0x23:  # new-array
            assert types[units[pc + 1]].startswith("["), "new-array needs an array type"
            v[(u >> 8) & 0xF] = [None] * v[u >> 12]
            pc += 2
        elif op == 0x1F:  # check-cast: only checks the register is set
            pc += 2
        elif op == 0x28:  # goto
            off = u >> 8
            pc += off - 256 if off > 127 else off
        elif op == 0x35:  # if-ge
            a, b = (u >> 8) & 0xF, u >> 12
            off = units[pc + 1]
            off -= 65536 if off > 32767 else 0
            pc += off if v[a] >= v[b] else 2
        elif op == 0x46:  # aget-object
            arr, idx = units[pc + 1] & 0xFF, units[pc + 1] >> 8
            v[u >> 8] = v[arr][v[idx]]
            pc += 2
        elif op in (0x54, 0x5B):  # iget-object / iput-object
            a, b = (u >> 8) & 0xF, u >> 12
            fcls, fname, ftype = fields[units[pc + 1]]
            assert fcls == TSB and v[b] is not None
            if op == 0x54:
                v[a] = v[b].f[fname]
            else:
                v[b].f[fname] = v[a]
            pc += 2
        elif op == 0xD8:  # add-int/lit8
            lit = units[pc + 1] >> 8
            v[u >> 8] = v[units[pc + 1] & 0xFF] + (lit - 256 if lit > 127 else lit)
            pc += 2
        elif op in (0x6E, 0x70, 0x71):  # invoke-virtual / direct / static
            count, g = u >> 12, (u >> 8) & 0xF
            mid = units[pc + 1]
            w = units[pc + 2]
            regs_used = [w & 0xF, (w >> 4) & 0xF, (w >> 8) & 0xF, (w >> 12) & 0xF, g][:count]
            result = invoke(methods[mid], [v[r] for r in regs_used])
            pc += 3
        else:
            raise AssertionError("opcode 0x%02x is not supported by the test interpreter" % op)


def call(name, params, args, ret=None):
    acc, regs, ins, outs, units = code_of(name, params, ret)
    return run(acc, regs, ins, units, args)


CTX, INTENT, BUNDLE = "Landroid/content/Context;", "Landroid/content/Intent;", "Landroid/os/Bundle;"
ctx, a, b, c = Obj("ctx"), Obj("intentA"), Obj("intentB"), Obj("intentC")

# what DuckDuckGo does at start-up
tsb = call("create", (CTX,), [ctx])
assert tsb.cls == TSB and tsb.f["mSourceContext"] is ctx
assert call("addNextIntent", (INTENT,), [tsb, a]) is tsb, "addNextIntent returns this"
assert call("addNextIntent", (INTENT,), [tsb, b]) is tsb
intents = call("getIntents", (), [tsb])
assert intents == [a, b], intents
assert call("getIntentCount", (), [tsb]) == 2
assert call("editIntentAt", ("I",), [tsb, 1]) is b

# the other builders return this and add what they are given
assert call("addNextIntentWithParentStack", (INTENT,), [tsb, c]) is tsb
assert call("getIntentCount", (), [tsb]) == 3
for p in ("Landroid/app/Activity;", "Ljava/lang/Class;", "Landroid/content/ComponentName;"):
    assert call("addParentStack", (p,), [tsb, Obj("x")]) is tsb
assert call("getIntentCount", (), [tsb]) == 3

# startActivities starts every intent, in order, from the app's context
calls.clear()
call("startActivities", (), [tsb])
assert calls == [("startActivity", a, None), ("startActivity", b, None), ("startActivity", c, None)], calls
calls.clear()
bundle = Obj("bundle")
call("startActivities", (BUNDLE,), [tsb, bundle])
assert [x[1] for x in calls] == [a, b, c] and all(x[2] is bundle for x in calls)

# an empty builder: no intents, nothing started
empty = call("create", (CTX,), [ctx])
assert call("getIntents", (), [empty]) == [] and call("getIntentCount", (), [empty]) == 0
calls.clear()
call("startActivities", (), [empty])
assert calls == []

# getPendingIntent goes through PendingIntent.getActivities(context, requestCode, intents, flags, options)
calls.clear()
assert call("getPendingIntent", ("I", "I"), [tsb, 7, 134217728]) == "PENDING"
assert calls == [("getActivities", ctx, 7, [a, b, c], 134217728, None)], calls
calls.clear()
assert call("getPendingIntent", ("I", "I", BUNDLE), [tsb, 9, 1, bundle]) == "PENDING"
assert calls == [("getActivities", ctx, 9, [a, b, c], 1, bundle)], calls
print("test_compat_dex (the real bytecode runs: create/addNextIntent/getIntents/startActivities/getPendingIntent): OK")
