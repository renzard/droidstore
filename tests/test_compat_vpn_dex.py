"""compat/atl-compat-vpn.jar (android.net.VpnService + android.net.VpnService$Builder) and compat/atl-compat-autofill.jar
(android.service.autofill.AutofillService) are valid dex and do their job.

No ART is available in the tests, so on the REAL bytes of the committed jar this file
  1) parses every dex strictly, with the rules ART's verifier applies (checksum, SHA-1, "size 0 means offset 0", sorted ids,
     proto shorties, class data, code items, a map list that matches) and decodes every instruction of every method
     (length, register numbers, argument counts of invokes against the method's prototype);
  2) runs the methods in a tiny Dalvik interpreter (only the opcodes the classes use).

    python3 tests/test_compat_vpn_dex.py
"""
import hashlib
import os
import struct
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(ROOT, "compat", "atl-compat-vpn.jar")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_compat_dex as gen  # noqa: E402

assert open(JAR, "rb").read() == gen.build_jar(gen.JARS["atl-compat-vpn.jar"]), \
    "compat/atl-compat-vpn.jar is out of date: run tools/make_compat_dex.py"
with zipfile.ZipFile(JAR) as z:
    assert z.namelist() == ["classes.dex", "classes2.dex"], z.namelist()
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    DEX = [z.read("classes.dex"), z.read("classes2.dex")]

# instruction length (16-bit units) of every opcode the compat classes may use
LENGTH = {0x0E: 1, 0x0F: 1, 0x11: 1, 0x12: 1, 0x6E: 3, 0x70: 3, 0x71: 3}


class Dex:
    def __init__(self, dex):
        self.dex = dex
        d = dex
        u32 = lambda off: struct.unpack_from("<I", d, off)[0]  # noqa: E731
        u16 = lambda off: struct.unpack_from("<H", d, off)[0]  # noqa: E731
        assert d[:8] == b"dex\n035\0"
        assert u32(8) == zlib.adler32(d[12:]) & 0xFFFFFFFF, "adler32"
        assert d[12:32] == hashlib.sha1(d[32:]).digest(), "sha1"
        assert u32(32) == len(d) and u32(36) == 0x70 and u32(40) == 0x12345678
        assert u32(44) == 0 and u32(48) == 0, "no link section"
        map_off = u32(52)
        sec = {}
        for name, pos in (("str", 56), ("type", 64), ("proto", 72), ("field", 80), ("method", 88), ("class", 96)):
            n, off = u32(pos), u32(pos + 4)
            if n == 0:
                assert off == 0, "%s ids: offset must be 0 when the size is 0 (ART rejects it otherwise)" % name
            sec[name] = (n, off)
        data_size, data_off = u32(104), u32(108)
        assert data_off + data_size == len(d) and data_size % 4 == 0
        n_str, str_off = sec["str"]
        n_type, type_off = sec["type"]
        n_proto, proto_off = sec["proto"]
        n_field, field_off = sec["field"]
        n_method, method_off = sec["method"]
        n_class, class_off = sec["class"]
        assert str_off == 0x70 and type_off == str_off + 4 * n_str and proto_off == type_off + 4 * n_type
        assert n_class == 1 and data_off == method_off + 8 * n_method + 32 == class_off + 32
        assert method_off == proto_off + 12 * n_proto + 8 * n_field, "tables follow each other"

        def uleb(off):
            value = shift = 0
            while True:
                b = d[off]
                off += 1
                value |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    return value, off

        self.strings = []
        for i in range(n_str):
            off = u32(str_off + 4 * i)
            assert data_off <= off < len(d)
            size, p = uleb(off)
            end = d.index(b"\0", p)
            assert end - p == size
            self.strings.append(d[p:end].decode("ascii"))
        assert self.strings == sorted(self.strings) and len(set(self.strings)) == n_str, "string ids sorted, unique"
        ids = [u32(type_off + 4 * i) for i in range(n_type)]
        assert ids == sorted(ids) and len(set(ids)) == n_type, "type ids sorted by string, unique"
        self.types = [self.strings[i] for i in ids]
        self.protos = []
        keys = []
        for i in range(n_proto):
            shorty, ret, plist = struct.unpack_from("<III", d, proto_off + 12 * i)
            params = []
            if plist:
                assert plist % 4 == 0
                params = [self.types[u16(plist + 4 + 2 * k)] for k in range(u32(plist))]
            short = lambda t: "L" if t[0] in "L[" else t  # noqa: E731
            assert self.strings[shorty] == short(self.types[ret]) + "".join(short(p) for p in params)
            self.protos.append((self.types[ret], tuple(params)))
            keys.append((ret, tuple(u16(plist + 4 + 2 * k) for k in range(len(params))) if plist else ()))
        assert keys == sorted(keys) and len(set(self.protos)) == n_proto, "proto ids sorted, unique"
        self.fields = [struct.unpack_from("<HHI", d, field_off + 8 * i) for i in range(n_field)]
        assert self.fields == sorted(self.fields)
        raw = [struct.unpack_from("<HHI", d, method_off + 8 * i) for i in range(n_method)]
        assert raw == sorted(raw, key=lambda m: (m[0], m[2], m[1])), "method ids sorted by class, name, proto"
        assert len(set(raw)) == n_method
        self.methods = [(self.types[c], self.strings[n], self.protos[p]) for c, p, n in raw]
        cls, flags, sup, ifaces, src, annos, cdata, static_vals = struct.unpack_from("<8I", d, class_off)
        self.cls, self.super = self.types[cls], self.types[sup]
        assert flags == 1 and ifaces == 0 and src == 0xFFFFFFFF and annos == 0 and static_vals == 0

        p = cdata
        n_static, p = uleb(p)
        n_inst, p = uleb(p)
        n_direct, p = uleb(p)
        n_virtual, p = uleb(p)
        assert n_static == 0 and n_inst == 0 == n_field
        self.code = {}
        for group, n in (("direct", n_direct), ("virtual", n_virtual)):
            prev = 0
            for _ in range(n):
                diff, p = uleb(p)
                acc, p = uleb(p)
                code_off, p = uleb(p)
                prev += diff
                m = self.methods[prev]
                assert m[0] == self.cls, "the class defines only its own methods"
                assert code_off % 4 == 0 and code_off >= data_off
                if group == "direct":
                    assert acc & 0x8 or (acc & 0x10000 and m[1] == "<init>")
                else:
                    assert not acc & (0x8 | 0x2 | 0x10000) and acc & 1
                self.code[m] = (acc, self._code_item(code_off, m, acc))
        assert set(self.code) == {m for m in self.methods if m[0] == self.cls}, "every method of the class has code"

        n_items = u32(map_off)
        assert map_off % 4 == 0 and map_off + 4 + 12 * n_items == len(d)
        last, kinds = -1, []
        for i in range(n_items):
            kind, _u, count, off = struct.unpack_from("<HHII", d, map_off + 4 + 12 * i)
            assert count > 0, "no empty map items"
            assert off > last or (kind == 0 and off == 0)
            last = off
            kinds.append(kind)
        assert len(set(kinds)) == len(kinds) and kinds[0] == 0 and kinds[-1] == 0x1000
        assert (0x0004 in kinds) == (n_field > 0)

    def _code_item(self, off, method, acc):
        d = self.dex
        regs, ins, outs, tries, dbg, size = struct.unpack_from("<HHHHII", d, off)
        assert tries == 0 and dbg == 0
        units = list(struct.unpack_from("<%dH" % size, d, off + 16))
        params = method[2][1]
        assert ins == len(params) + (0 if acc & 0x8 else 1), "ins_size = parameters (+ this)"
        assert regs >= ins
        pc, max_out = 0, 0
        while pc < size:
            u = units[pc]
            op = u & 0xFF
            assert op in LENGTH, "opcode 0x%02x is not allowed here" % op
            if op in (0x6E, 0x70, 0x71):
                count = u >> 12
                callee = self.methods[units[pc + 1]]
                want = len(callee[2][1]) + (0 if op == 0x71 else 1)
                assert count == want, "%s: %d argument registers, prototype wants %d" % (callee, count, want)
                w = units[pc + 2]
                used = [w & 0xF, (w >> 4) & 0xF, (w >> 8) & 0xF, (w >> 12) & 0xF, (u >> 8) & 0xF][:count]
                assert all(r < regs for r in used)
                max_out = max(max_out, count)
            else:
                assert (u >> 8) & 0xFF < regs or op == 0x0E, "register out of range"
            pc += LENGTH[op]
        assert pc == size, "the instructions end exactly at the end of the code item"
        assert units[-1] & 0xFF in (0x0E, 0x0F, 0x11) or units[size - 1] == units[-1]
        assert outs == max_out, (outs, max_out)
        return regs, ins, units

    def run(self, name, params, args, ret=None):
        for m, (acc, (regs, ins, units)) in self.code.items():
            if m[1] == name and m[2][1] == tuple(params) and (ret is None or m[2][0] == ret):
                return self._run(regs, ins, units, args)
        raise KeyError(name)

    def _run(self, regs, ins, units, args):
        assert len(args) == ins
        v = [None] * regs
        v[regs - ins:] = args
        pc = 0
        while True:
            u = units[pc]
            op = u & 0xFF
            if op == 0x0E:
                return None
            if op in (0x0F, 0x11):
                return v[u >> 8]
            if op == 0x12:
                v[(u >> 8) & 0xF] = u >> 12
                pc += 1
            else:  # invoke-direct of a constructor: Service.<init> / Object.<init>
                callee = self.methods[units[pc + 1]]
                assert op == 0x70 and callee[1] == "<init>" and callee[0] in (self.super, "Ljava/lang/Object;"), callee
                CALLS.append(callee[0])
                pc += 3


CALLS = []
NULL = 0  # Dalvik: null is the register value 0
vpn, builder = Dex(DEX[0]), Dex(DEX[1])
assert vpn.cls == gen.VPN and vpn.super == gen.SERVICE
assert builder.cls == gen.VPN_BUILDER and builder.super == "Ljava/lang/Object;"
print("test_compat_vpn_dex (strict parse + decode of %d + %d bytes): OK" % (len(DEX[0]), len(DEX[1])))

# ---------------------------------------------------------------- behaviour: the class exists and the VPN just "does not start"
class O:
    pass


svc, ctx, intent = O(), O(), O()
assert vpn.run("<init>", (), [svc]) is None and CALLS == ["Landroid/app/Service;"], "the constructor calls Service()"
assert vpn.run("prepare", (gen.CONTEXT,), [ctx]) == NULL, "prepare(): nothing to ask the user"
assert vpn.run("onBind", (gen.INTENT,), [svc, intent]) == NULL
assert vpn.run("onRevoke", (), [svc]) is None
for p in ("I", gen.SOCKET, gen.DGRAM):
    assert vpn.run("protect", (p,), [svc, 5 if p == "I" else O()]) == 0, "protect() reports false"
assert vpn.run("setUnderlyingNetworks", (gen.NETWORK_ARR,), [svc, None]) == 0
assert vpn.run("isAlwaysOn", (), [svc]) == 0 and vpn.run("isLockdownEnabled", (), [svc]) == 0

CALLS.clear()
b = O()
assert builder.run("<init>", (gen.VPN,), [b, svc]) is None and CALLS == ["Ljava/lang/Object;"]
n_setters = 0
for (cls, name, (ret, params)), (acc, code) in builder.code.items():
    if name in ("<init>", "establish"):
        continue
    assert ret == gen.VPN_BUILDER, name
    fake = [O() if p[0] in "L[" else 1 for p in params]
    assert builder.run(name, params, [b] + fake, ret) is b, "%s returns the builder (calls can be chained)" % name
    n_setters += 1
assert n_setters == len(gen._BUILDER_SETTERS) == 17
assert builder.run("establish", (), [b]) == NULL, "establish() is null: 'VPN not prepared / revoked'"
# the chain DuckDuckGo-style code writes: Builder(this).addAddress(..).addRoute(..).setSession(..).establish()
chain = builder.run("addAddress", ("Ljava/lang/String;", "I"), [b, "10.0.0.2", 32])
chain = builder.run("addRoute", ("Ljava/lang/String;", "I"), [chain, "0.0.0.0", 0])
chain = builder.run("setSession", ("Ljava/lang/String;",), [chain, "x"])
assert builder.run("establish", (), [chain]) == NULL
print("test_compat_vpn_dex (the real bytecode runs: prepare/protect/Builder chain/establish): OK")


# ---------------------------------------------------------------- android.service.autofill.AutofillService
AJAR = os.path.join(ROOT, "compat", "atl-compat-autofill.jar")
assert open(AJAR, "rb").read() == gen.build_jar(gen.JARS["atl-compat-autofill.jar"]), \
    "compat/atl-compat-autofill.jar is out of date: run tools/make_compat_dex.py"
with zipfile.ZipFile(AJAR) as z:
    assert z.namelist() == ["classes.dex"] and z.getinfo("classes.dex").compress_type == zipfile.ZIP_STORED
    af = Dex(z.read("classes.dex"))
assert af.cls == gen.AUTOFILL and af.super == gen.SERVICE
CALLS.clear()
assert af.run("<init>", (), [O()]) is None and CALLS == ["Landroid/app/Service;"]
assert af.run("onBind", (gen.INTENT,), [O(), O()]) == NULL
assert af.run("onConnected", (), [O()]) is None and af.run("onDisconnect", (), [O()]) is None
print("test_compat_vpn_dex (AutofillService: strict parse, constructor calls Service(), onBind null): OK")
