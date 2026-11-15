#!/usr/bin/env python3
"""Builds the jars in compat/: atl-compat.jar (android.app.TaskStackBuilder), atl-compat-vpn.jar (android.net.VpnService and
its Builder) and atl-compat-autofill.jar (android.service.autofill.AutofillService), one dex per class.

Why: an ATL that was built before atl/src/api-impl/android/app/TaskStackBuilder.java existed (for example the
downloaded atl-touch SDK) does not have the class, and DuckDuckGo dies at start-up with
"NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;". Rebuilding ATL is the real fix, but
until then the store adds this dex to the APK as one more classesN.dex (the app's own class loader reads all of them,
whatever ATL version runs), and only for an APK that really uses the class and only when ATL lacks it
(src/store.py: _embed_compat_dex). Passing the jar as an extra file on ATL's command line was tried first: the
downloaded SDK ignored it.

This file writes the dex by hand (no Android SDK is needed, only Python 3). The result is byte-for-byte the same on
every run, so the committed jar can be checked:   python3 tools/make_compat_dex.py --check
The behaviour is tested by tests/test_compat_dex.py (a small Dalvik interpreter runs the real bytes).

The class is the same API as atl/src/api-impl/android/app/TaskStackBuilder.java, simplified: the parent stack of an
activity is not resolved (addParentStack does nothing) and the intents are not copied or flagged.
"""
import hashlib
import os
import struct
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "compat", "atl-compat.jar")

TSB = "Landroid/app/TaskStackBuilder;"
ACTIVITY = "Landroid/app/Activity;"
PENDING = "Landroid/app/PendingIntent;"
COMPONENT = "Landroid/content/ComponentName;"
CONTEXT = "Landroid/content/Context;"
INTENT = "Landroid/content/Intent;"
BUNDLE = "Landroid/os/Bundle;"
CLASS = "Ljava/lang/Class;"
OBJECT = "Ljava/lang/Object;"
ARRAYLIST = "Ljava/util/ArrayList;"
INTENT_ARR = "[Landroid/content/Intent;"
OBJECT_ARR = "[Ljava/lang/Object;"

ACC_PUBLIC, ACC_PRIVATE, ACC_STATIC, ACC_CONSTRUCTOR = 0x1, 0x2, 0x8, 0x10000

F_INTENTS = ("f", TSB, "mIntents", ARRAYLIST)
F_CONTEXT = ("f", TSB, "mSourceContext", CONTEXT)


def M(cls, name, ret, *params):
    return ("m", cls, name, ret, tuple(params))


M_OBJECT_INIT = M(OBJECT, "<init>", "V")
M_LIST_INIT = M(ARRAYLIST, "<init>", "V")
M_LIST_ADD = M(ARRAYLIST, "add", "Z", OBJECT)
M_LIST_GET = M(ARRAYLIST, "get", OBJECT, "I")
M_LIST_SIZE = M(ARRAYLIST, "size", "I")
M_LIST_TOARRAY = M(ARRAYLIST, "toArray", OBJECT_ARR, OBJECT_ARR)
M_START = M(CONTEXT, "startActivity", "V", INTENT, BUNDLE)
M_GET_ACTIVITIES = M(PENDING, "getActivities", PENDING, CONTEXT, "I", INTENT_ARR, "I", BUNDLE)
M_TSB_INIT = M(TSB, "<init>", "V", CONTEXT)
M_TSB_GETINTENTS = M(TSB, "getIntents", INTENT_ARR)
M_TSB_START_B = M(TSB, "startActivities", "V", BUNDLE)
M_TSB_PI3 = M(TSB, "getPendingIntent", PENDING, "I", "I", BUNDLE)

# (name, return type, parameter types, access flags, registers, code)
# Code: ("op", args...). Registers: locals first, then the parameters (this is the first parameter of a method).
METHODS = [
    ("<init>", "V", (CONTEXT,), ACC_PRIVATE | ACC_CONSTRUCTOR, 3, [
        ("invoke-direct", [1], M_OBJECT_INIT),
        ("new-instance", 0, ARRAYLIST),
        ("invoke-direct", [0], M_LIST_INIT),
        ("iput-object", 0, 1, F_INTENTS),
        ("iput-object", 2, 1, F_CONTEXT),
        ("return-void",),
    ]),
    ("create", TSB, (CONTEXT,), ACC_PUBLIC | ACC_STATIC, 2, [
        ("new-instance", 0, TSB),
        ("invoke-direct", [0, 1], M_TSB_INIT),
        ("return-object", 0),
    ]),
    ("addNextIntent", TSB, (INTENT,), ACC_PUBLIC, 3, [
        ("iget-object", 0, 1, F_INTENTS),
        ("invoke-virtual", [0, 2], M_LIST_ADD),
        ("return-object", 1),
    ]),
    ("addNextIntentWithParentStack", TSB, (INTENT,), ACC_PUBLIC, 3, [
        ("iget-object", 0, 1, F_INTENTS),
        ("invoke-virtual", [0, 2], M_LIST_ADD),
        ("return-object", 1),
    ]),
    ("addParentStack", TSB, (ACTIVITY,), ACC_PUBLIC, 2, [("return-object", 0)]),
    ("addParentStack", TSB, (CLASS,), ACC_PUBLIC, 2, [("return-object", 0)]),
    ("addParentStack", TSB, (COMPONENT,), ACC_PUBLIC, 2, [("return-object", 0)]),
    ("editIntentAt", INTENT, ("I",), ACC_PUBLIC, 3, [
        ("iget-object", 0, 1, F_INTENTS),
        ("invoke-virtual", [0, 2], M_LIST_GET),
        ("move-result-object", 0),
        ("check-cast", 0, INTENT),
        ("return-object", 0),
    ]),
    ("getIntentCount", "I", (), ACC_PUBLIC, 2, [
        ("iget-object", 0, 1, F_INTENTS),
        ("invoke-virtual", [0], M_LIST_SIZE),
        ("move-result", 0),
        ("return", 0),
    ]),
    ("getIntents", INTENT_ARR, (), ACC_PUBLIC, 3, [
        ("iget-object", 0, 2, F_INTENTS),
        ("invoke-virtual", [0], M_LIST_SIZE),
        ("move-result", 1),
        ("new-array", 1, 1, INTENT_ARR),
        ("invoke-virtual", [0, 1], M_LIST_TOARRAY),
        ("move-result-object", 0),
        ("check-cast", 0, INTENT_ARR),
        ("return-object", 0),
    ]),
    ("getPendingIntent", PENDING, ("I", "I"), ACC_PUBLIC, 4, [
        ("const/4", 0, 0),
        ("invoke-virtual", [1, 2, 3, 0], M_TSB_PI3),
        ("move-result-object", 0),
        ("return-object", 0),
    ]),
    ("getPendingIntent", PENDING, ("I", "I", BUNDLE), ACC_PUBLIC, 6, [
        ("iget-object", 0, 2, F_CONTEXT),
        ("invoke-virtual", [2], M_TSB_GETINTENTS),
        ("move-result-object", 1),
        ("invoke-static", [0, 3, 1, 4, 5], M_GET_ACTIVITIES),
        ("move-result-object", 0),
        ("return-object", 0),
    ]),
    ("startActivities", "V", (), ACC_PUBLIC, 2, [
        ("const/4", 0, 0),
        ("invoke-virtual", [1, 0], M_TSB_START_B),
        ("return-void",),
    ]),
    ("startActivities", "V", (BUNDLE,), ACC_PUBLIC, 7, [
        ("invoke-virtual", [5], M_TSB_GETINTENTS),
        ("move-result-object", 0),
        ("array-length", 1, 0),
        ("const/4", 2, 0),
        ("label", "loop"),
        ("if-ge", 2, 1, "done"),
        ("aget-object", 3, 0, 2),
        ("iget-object", 4, 5, F_CONTEXT),
        ("invoke-virtual", [4, 3, 6], M_START),
        ("add-int/lit8", 2, 2, 1),
        ("goto", "loop"),
        ("label", "done"),
        ("return-void",),
    ]),
]


TSB_SPEC = {"class": TSB, "super": OBJECT, "fields": [F_INTENTS, F_CONTEXT], "methods": METHODS}

# ----------------------------------------------------------------------------------------------- android.net.VpnService
# DuckDuckGo's App Tracking Protection service extends it, and DuckDuckGo's dependency-injection component refers to the
# class at start-up ("NoClassDefFoundError: Failed resolution of: Landroid/net/VpnService;"). ATL cannot create a VPN
# interface (no tun device is handed to an app), so this is the honest minimum: the class exists and can be extended;
# prepare() says "nothing to ask the user", Builder.establish() returns null, which is how Android itself reports "the
# VPN was not prepared or was revoked": the app's VPN feature then simply does not start. Same API as
# atl/src/api-impl/android/net/VpnService.java.
VPN = "Landroid/net/VpnService;"
VPN_BUILDER = "Landroid/net/VpnService$Builder;"
SERVICE = "Landroid/app/Service;"
IBINDER = "Landroid/os/IBinder;"
PFD = "Landroid/os/ParcelFileDescriptor;"
NETWORK_ARR = "[Landroid/net/Network;"
SOCKET = "Ljava/net/Socket;"
DGRAM = "Ljava/net/DatagramSocket;"
INETADDR = "Ljava/net/InetAddress;"
STRING = "Ljava/lang/String;"

M_SERVICE_INIT = M(SERVICE, "<init>", "V")

_FALSE = [("const/4", 0, 0), ("return", 0)]  # return false
_NULL = [("const/4", 0, 0), ("return-object", 0)]  # return null

VPN_METHODS = [
    ("<init>", "V", (), ACC_PUBLIC | ACC_CONSTRUCTOR, 1, [
        ("invoke-direct", [0], M_SERVICE_INIT),
        ("return-void",),
    ]),
    ("prepare", INTENT, (CONTEXT,), ACC_PUBLIC | ACC_STATIC, 2, list(_NULL)),
    ("onBind", IBINDER, (INTENT,), ACC_PUBLIC, 3, list(_NULL)),
    ("onRevoke", "V", (), ACC_PUBLIC, 1, [("return-void",)]),
    ("protect", "Z", ("I",), ACC_PUBLIC, 3, list(_FALSE)),
    ("protect", "Z", (SOCKET,), ACC_PUBLIC, 3, list(_FALSE)),
    ("protect", "Z", (DGRAM,), ACC_PUBLIC, 3, list(_FALSE)),
    ("setUnderlyingNetworks", "Z", (NETWORK_ARR,), ACC_PUBLIC, 3, list(_FALSE)),
    ("isAlwaysOn", "Z", (), ACC_PUBLIC, 2, list(_FALSE)),
    ("isLockdownEnabled", "Z", (), ACC_PUBLIC, 2, list(_FALSE)),
]
VPN_SPEC = {"class": VPN, "super": SERVICE, "fields": [], "methods": VPN_METHODS}

# VpnService.Builder: every setter keeps nothing and returns the builder (no locals: `this` is register 0).
_BUILDER_SETTERS = [
    ("addAddress", (STRING, "I")), ("addAddress", (INETADDR, "I")),
    ("addRoute", (STRING, "I")), ("addRoute", (INETADDR, "I")),
    ("addDnsServer", (STRING,)), ("addDnsServer", (INETADDR,)),
    ("addSearchDomain", (STRING,)),
    ("addAllowedApplication", (STRING,)), ("addDisallowedApplication", (STRING,)),
    ("allowBypass", ()), ("allowFamily", ("I",)),
    ("setBlocking", ("Z",)), ("setMetered", ("Z",)), ("setMtu", ("I",)), ("setSession", (STRING,)),
    ("setConfigureIntent", (PENDING,)), ("setUnderlyingNetworks", (NETWORK_ARR,)),
]
BUILDER_METHODS = [
    ("<init>", "V", (VPN,), ACC_PUBLIC | ACC_CONSTRUCTOR, 2, [
        ("invoke-direct", [0], M_OBJECT_INIT),
        ("return-void",),
    ]),
] + [
    (name, VPN_BUILDER, params, ACC_PUBLIC, 1 + len(params), [("return-object", 0)])
    for name, params in _BUILDER_SETTERS
] + [
    ("establish", PFD, (), ACC_PUBLIC, 2, list(_NULL)),
]
BUILDER_SPEC = {"class": VPN_BUILDER, "super": OBJECT, "fields": [], "methods": BUILDER_METHODS}

# ----------------------------------------------------------------------------------------------- android.service.autofill.AutofillService
# DuckDuckGo's autofill service extends it and its dependency-injection component refers to the class at start-up
# ("NoClassDefFoundError: Failed resolution of: Landroid/service/autofill/AutofillService;"). ATL has no autofill
# framework: the class only exists, and nobody ever calls the service. The abstract onFillRequest/onSaveRequest are left
# out on purpose (their parameter classes do not exist in ATL either; the app's subclass defines them itself).
# Same API as atl/src/api-impl/android/service/autofill/AutofillService.java.
AUTOFILL = "Landroid/service/autofill/AutofillService;"
AUTOFILL_METHODS = [
    ("<init>", "V", (), ACC_PUBLIC | ACC_CONSTRUCTOR, 1, [
        ("invoke-direct", [0], M_SERVICE_INIT),
        ("return-void",),
    ]),
    ("onBind", IBINDER, (INTENT,), ACC_PUBLIC, 3, list(_NULL)),
    ("onConnected", "V", (), ACC_PUBLIC, 1, [("return-void",)]),
    ("onDisconnect", "V", (), ACC_PUBLIC, 1, [("return-void",)]),
]
AUTOFILL_SPEC = {"class": AUTOFILL, "super": SERVICE, "fields": [], "methods": AUTOFILL_METHODS}

# jar file -> the classes in it, one dex file per class (classes.dex, classes2.dex, ...)
JARS = {
    "atl-compat.jar": [TSB_SPEC],
    "atl-compat-vpn.jar": [VPN_SPEC, BUILDER_SPEC],
    "atl-compat-autofill.jar": [AUTOFILL_SPEC],
}


# ----------------------------------------------------------------------------------------------- dex writer
def uleb(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def shorty_char(t):
    return "L" if t[0] in "L[" else t


def align(buf, n):
    while len(buf) % n:
        buf.append(0)


def _refs_of(code):
    for ins in code:
        for part in ins[1:]:
            if isinstance(part, tuple) and part and part[0] in ("m", "f"):
                yield part


def build_dex(spec=None):
    spec = spec or TSB_SPEC
    cls_desc, super_desc = spec["class"], spec["super"]
    class_fields, class_methods = spec["fields"], spec["methods"]
    # ---- 1) collect everything the file refers to
    types, fields, methods, protos = set(), set(), set(), set()
    types.update((cls_desc, super_desc))

    def add_proto(ret, params):
        protos.add((ret, tuple(params)))
        types.add(ret)
        types.update(params)

    for name, ret, params, _acc, _regs, code in class_methods:
        methods.add((cls_desc, name, ret, tuple(params)))
        add_proto(ret, params)
        for ins in code:
            if ins[0] in ("new-instance", "check-cast"):
                types.add(ins[2])
            elif ins[0] == "new-array":
                types.add(ins[3])
        for ref in _refs_of(code):
            if ref[0] == "f":
                fields.add(ref[1:])
                types.update((ref[1], ref[3]))
            else:
                methods.add(ref[1:])
                types.add(ref[1])
                add_proto(ref[3], ref[4])
    fields.update(f[1:] for f in class_fields)

    strings = set(types)
    for cls, name, typ in fields:
        strings.add(name)
    for cls, name, ret, params in methods:
        strings.add(name)
    for ret, params in protos:
        strings.add(ret_shorty(ret, params))
    strings = sorted(strings)
    sidx = {s: i for i, s in enumerate(strings)}
    type_list = sorted(types, key=lambda t: sidx[t])
    tidx = {t: i for i, t in enumerate(type_list)}
    proto_list = sorted(protos, key=lambda p: (tidx[p[0]], [tidx[x] for x in p[1]]))
    pidx = {p: i for i, p in enumerate(proto_list)}
    field_list = sorted(fields, key=lambda f: (tidx[f[0]], sidx[f[1]], tidx[f[2]]))
    fidx = {f: i for i, f in enumerate(field_list)}
    method_list = sorted(methods, key=lambda m: (tidx[m[0]], sidx[m[1]], pidx[(m[2], tuple(m[3]))]))
    midx = {m: i for i, m in enumerate(method_list)}

    # ---- 2) code
    codes = {}
    for name, ret, params, acc, regs, code in class_methods:
        static = bool(acc & ACC_STATIC)
        ins_size = len(params) + (0 if static else 1)
        units, outs = assemble(code, fidx, midx, tidx)
        codes[(name, ret, tuple(params))] = (regs, ins_size, outs, units)

    # ---- 3) layout
    n_str, n_type, n_proto, n_field, n_method = len(strings), len(type_list), len(proto_list), len(field_list), len(method_list)
    string_ids_off = 0x70
    type_ids_off = string_ids_off + 4 * n_str
    proto_ids_off = type_ids_off + 4 * n_type
    field_ids_off = proto_ids_off + 12 * n_proto
    method_ids_off = field_ids_off + 8 * n_field
    class_defs_off = method_ids_off + 8 * n_method
    data_off = class_defs_off + 32

    data = bytearray()

    def here():
        return data_off + len(data)

    # type lists (4-aligned): one per proto that has parameters
    tl_off = {}
    for p in proto_list:
        if p[1]:
            align(data, 4)
            tl_off[p] = here()
            data += struct.pack("<I", len(p[1])) + b"".join(struct.pack("<H", tidx[t]) for t in p[1])
    # code items (4-aligned)
    code_off, map_code_count, first_code = {}, 0, None
    order = sorted(class_methods, key=lambda m: midx[(cls_desc, m[0], m[1], tuple(m[2]))])
    for name, ret, params, acc, regs, code in order:
        align(data, 4)
        regs_, ins_size, outs, units = codes[(name, ret, tuple(params))]
        off = here()
        code_off[(name, ret, tuple(params))] = off
        if first_code is None:
            first_code = off
        map_code_count += 1
        data += struct.pack("<HHHHII", regs_, ins_size, outs, 0, 0, len(units))
        data += b"".join(struct.pack("<H", u) for u in units)
    # string data
    str_off = []
    first_str = here()
    for s in strings:
        str_off.append(here())
        raw = s.encode("ascii")
        data += uleb(len(raw)) + raw + b"\0"
    # class data
    first_class_data = here()
    inst = sorted([f[1:] for f in class_fields], key=lambda f: fidx[f])
    direct = sorted([m for m in class_methods if m[0] == "<init>" or m[3] & ACC_STATIC],
                    key=lambda m: midx[(cls_desc, m[0], m[1], tuple(m[2]))])
    virtual = sorted([m for m in class_methods if not (m[0] == "<init>" or m[3] & ACC_STATIC)],
                     key=lambda m: midx[(cls_desc, m[0], m[1], tuple(m[2]))])
    cd = bytearray()
    cd += uleb(0) + uleb(len(inst)) + uleb(len(direct)) + uleb(len(virtual))
    prev = 0
    for f in inst:
        cd += uleb(fidx[f] - prev) + uleb(ACC_PRIVATE)
        prev = fidx[f]
    for group in (direct, virtual):
        prev = 0
        for name, ret, params, acc, regs, code in group:
            i = midx[(cls_desc, name, ret, tuple(params))]
            cd += uleb(i - prev) + uleb(acc) + uleb(code_off[(name, ret, tuple(params))])
            prev = i
        # (the delta restarts at 0 for the virtual methods, as the format says)
    class_data_off = here()
    data += cd
    # map list
    align(data, 4)
    map_off = here()
    items = [
        (0x0000, 1, 0),
        (0x0001, n_str, string_ids_off),
        (0x0002, n_type, type_ids_off),
        (0x0003, n_proto, proto_ids_off),
        (0x0005, n_method, method_ids_off),
        (0x0006, 1, class_defs_off),
    ]
    if n_field:
        items.append((0x0004, n_field, field_ids_off))
    if tl_off:
        items.append((0x1001, len(tl_off), min(tl_off.values())))
    items.append((0x2001, map_code_count, first_code))
    items.append((0x2002, n_str, first_str))
    items.append((0x2000, 1, class_data_off))
    items.append((0x1000, 1, map_off))
    items.sort(key=lambda it: it[2])
    data += struct.pack("<I", len(items)) + b"".join(struct.pack("<HHII", t, 0, c, o) for t, c, o in items)

    # ---- 4) fixed tables
    head = bytearray()
    for off in str_off:
        head += struct.pack("<I", off)
    for t in type_list:
        head += struct.pack("<I", sidx[t])
    for p in proto_list:
        head += struct.pack("<III", sidx[ret_shorty(p[0], p[1])], tidx[p[0]], tl_off.get(p, 0))
    for cls, name, typ in field_list:
        head += struct.pack("<HHI", tidx[cls], tidx[typ], sidx[name])
    for cls, name, ret, params in method_list:
        head += struct.pack("<HHI", tidx[cls], pidx[(ret, tuple(params))], sidx[name])
    head += struct.pack("<IIIIIIII", tidx[cls_desc], ACC_PUBLIC, tidx[super_desc], 0, 0xFFFFFFFF, 0, class_data_off, 0)

    body = bytes(head) + bytes(data)
    file_size = 0x70 + len(body)
    header = bytearray(0x70)
    header[0:8] = b"dex\n035\0"
    struct.pack_into("<I", header, 32, file_size)
    struct.pack_into("<I", header, 36, 0x70)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<II", header, 44, 0, 0)  # link_size, link_off
    struct.pack_into("<I", header, 52, map_off)
    for pos, (n, off) in zip(range(56, 104, 8), [(n_str, string_ids_off), (n_type, type_ids_off), (n_proto, proto_ids_off),
                                                  (n_field, field_ids_off), (n_method, method_ids_off), (1, class_defs_off)]):
        struct.pack_into("<II", header, pos, n, off if n else 0)
    struct.pack_into("<II", header, 104, file_size - data_off, data_off)
    out = bytearray(header) + body
    assert len(out) == file_size
    struct.pack_into("<20s", out, 12, hashlib.sha1(bytes(out[32:])).digest())
    struct.pack_into("<I", out, 8, zlib.adler32(bytes(out[12:])) & 0xFFFFFFFF)
    return bytes(out)


def ret_shorty(ret, params):
    return shorty_char(ret) + "".join(shorty_char(p) for p in params)


# ----------------------------------------------------------------------------------------------- Dalvik assembler
OPS = {
    "move-result": 0x0A, "move-result-object": 0x0C, "return-void": 0x0E, "return": 0x0F, "return-object": 0x11,
    "const/4": 0x12, "array-length": 0x21, "new-instance": 0x22, "new-array": 0x23, "check-cast": 0x1F,
    "goto": 0x28, "if-ge": 0x35, "aget-object": 0x46, "iget-object": 0x54, "iput-object": 0x5B,
    "invoke-virtual": 0x6E, "invoke-direct": 0x70, "invoke-static": 0x71, "add-int/lit8": 0xD8,
}
SIZE = {"move-result": 1, "move-result-object": 1, "return-void": 1, "return": 1, "return-object": 1, "const/4": 1,
        "array-length": 1, "new-instance": 2, "new-array": 2, "check-cast": 2, "goto": 1, "if-ge": 2, "aget-object": 2,
        "iget-object": 2, "iput-object": 2, "invoke-virtual": 3, "invoke-direct": 3, "invoke-static": 3, "add-int/lit8": 2}


def assemble(code, fidx, midx, tidx):
    labels, pc = {}, 0
    for ins in code:
        if ins[0] == "label":
            labels[ins[1]] = pc
        else:
            pc += SIZE[ins[0]]
    units, pc, outs = [], 0, 0
    for ins in code:
        op = ins[0]
        if op == "label":
            continue
        o = OPS[op]
        if op in ("return-void",):
            units.append(o)
        elif op in ("move-result", "move-result-object", "return", "return-object"):
            units.append((ins[1] << 8) | o)
        elif op == "const/4":
            assert 0 <= ins[2] <= 7 and ins[1] < 16
            units.append((ins[2] << 12) | (ins[1] << 8) | o)
        elif op == "array-length":
            units.append((ins[2] << 12) | (ins[1] << 8) | o)
        elif op in ("new-instance", "check-cast"):
            units += [(ins[1] << 8) | o, tidx[ins[2]]]
        elif op == "new-array":
            units += [(ins[2] << 12) | (ins[1] << 8) | o, tidx[ins[3]]]
        elif op in ("iget-object", "iput-object"):
            units += [(ins[2] << 12) | (ins[1] << 8) | o, fidx[ins[3][1:]]]
        elif op == "aget-object":
            units += [(ins[1] << 8) | o, (ins[3] << 8) | ins[2]]
        elif op == "add-int/lit8":
            units += [(ins[1] << 8) | o, ((ins[3] & 0xFF) << 8) | ins[2]]
        elif op == "goto":
            off = labels[ins[1]] - pc
            assert -128 <= off <= 127
            units.append(((off & 0xFF) << 8) | o)
        elif op == "if-ge":
            off = labels[ins[3]] - pc
            assert -32768 <= off <= 32767
            units += [(ins[2] << 12) | (ins[1] << 8) | o, off & 0xFFFF]
        elif op.startswith("invoke-"):
            regs = ins[1]
            assert 1 <= len(regs) <= 5 and all(r < 16 for r in regs)
            ref = ins[2]
            m = midx[ref[1:3] + (ref[3], tuple(ref[4]))]
            outs = max(outs, len(regs))
            padded = list(regs) + [0] * (5 - len(regs))
            c, d, e, f, g = padded
            units += [(len(regs) << 12) | (g << 8) | o, m, (f << 12) | (e << 8) | (d << 4) | c]
        else:
            raise ValueError(op)
        pc += SIZE[op]
    return units, outs


def build_jar(specs=None):
    """One jar with one dex per class: classes.dex, classes2.dex, ... (default: the TaskStackBuilder jar)."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i, spec in enumerate(specs or [TSB_SPEC], 1):
            info = zipfile.ZipInfo("classes.dex" if i == 1 else "classes%d.dex" % i, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            z.writestr(info, build_dex(spec))
    return buf.getvalue()


def main(argv):
    status = 0
    for name, specs in JARS.items():
        out = os.path.join(ROOT, "compat", name)
        jar = build_jar(specs)
        if "--check" in argv:
            try:
                with open(out, "rb") as f:
                    same = f.read() == jar
            except OSError:
                same = False
            print("compat/%s is %s" % (name, "up to date" if same else "OUT OF DATE: run tools/make_compat_dex.py"))
            status |= 0 if same else 1
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(jar)
        print("wrote %s (%d bytes, sha256 %s)" % (out, len(jar), hashlib.sha256(jar).hexdigest()[:16]))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
