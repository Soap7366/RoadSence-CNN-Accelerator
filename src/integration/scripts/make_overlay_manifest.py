"""Export a manifest only for the routed and simulation-checked local overlay."""
from pathlib import Path
import hashlib,json,re,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
art=ROOT/"artifacts"
for name in ("cnn.bit","cnn.hwh","build_info.txt"):
    if not (art/name).is_file():
        raise RuntimeError("Missing completed artifact: "+name)
info={}
for line in (art/"build_info.txt").read_text(encoding="utf-8").splitlines():
    if "=" in line:
        k,v=line.split("=",1);info[k]=v
if info.get("Vivado")!="2023.2" or info.get("part")!="xc7z020clg400-1" or info.get("clock")!="50 MHz":
    raise RuntimeError("Unexpected tool/part/clock")
setup=float(info["setup_slack_ns"]);hold=float(info["hold_slack_ns"])
if setup<0 or hold<0:
    raise RuntimeError("Routed timing did not pass")
bridge=(ROOT/"build/sim_bridge/xsim.log").read_text(encoding="utf-8",errors="replace")
network=(ROOT/"build/sim_network/xsim.log").read_text(encoding="utf-8",errors="replace")
if "PASS tb_axi_bridge: groups=9" not in bridge:
    raise RuntimeError("Missing AXI protocol regression")
samples=re.findall(r"PASS AXI network sample=(\d+) collision=(-?\d+) steering=(-?\d+)",network)
if len(samples)!=30 or [int(x[0]) for x in samples]!=list(range(30)) or "PASS tb_axi_network: samples=30 heads=60" not in network:
    raise RuntimeError("Missing 30-Golden AXI network regression")
up=ROOT/"upstream/systoliccnn_8c8dcfafc901f0c54276e6aaba86d817cb82538d"
provenance=json.loads((ROOT/"reports/source_provenance.json").read_text(encoding="utf-8"))
for name,expected in provenance["files"].items():
    data=(up/name).read_bytes()
    if len(data)!=expected["bytes"] or hashlib.sha256(data).hexdigest()!=expected["sha256"]:
        raise RuntimeError("Pinned upstream source changed: "+name)
identity="cnn_axi_accelerator_0"
mapping=set()
tree=ET.parse(art/"cnn.hwh")
for node in tree.iter():
    if node.tag.rsplit("}",1)[-1]!="MEMRANGE":
        continue
    if node.attrib.get("INSTANCE","").strip("/")!=identity:
        continue
    base=int(node.attrib["BASEVALUE"],0);high=int(node.attrib["HIGHVALUE"],0)
    mapping.add((base,high-base+1))
if len(mapping)!=1:
    raise RuntimeError("Cannot establish unique generated HWH mapping: "+repr(mapping))
base,size=mapping.pop()
if size!=8192:
    raise RuntimeError("Unexpected aperture")
files={}
for kind,name in (("bit","cnn.bit"),("hwh","cnn.hwh")):
    data=(art/name).read_bytes()
    files[kind]={"path":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
source_hashes={}
for path in sorted((ROOT/"rtl").glob("*.sv")):
    source_hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
manifest={
    "schema":"pscnn.overlay.v1",
    "model_id":"dual_task_baseline2_int8_qat@619308c77fd9",
    "quant_params_sha256":"346bafa31f56bd5a28bff0d8ee7d8cd850e5e2da7f56e536037e46d033dc4225",
    "model_commit":"619308c77fd9af7d03bc89c494a33dda5d07386d",
    "rtl_commit":"8c8dcfafc901f0c54276e6aaba86d817cb82538d",
    "part":"xc7z020clg400-1","clock_mhz":50,"abi_version":65536,"magic":0x53434E4E,
    "ip_name":identity,"hwh_phys_addr":base,"hwh_addr_range":size,"files":files,
    "build_verified":{"setup_slack_ns":setup,"hold_slack_ns":hold,"rtl_golden_samples":30,"protocol_groups":9},
    "integration_source_sha256":source_hashes,
    "source_provenance_sha256":hashlib.sha256((ROOT/"reports/source_provenance.json").read_bytes()).hexdigest(),
    "board_verified":False,
    "note":"This manifest identifies local build and simulation evidence. Physical inference is verified separately."
}
with (art/"overlay_manifest.json").open("x",encoding="utf-8",newline="\n") as f:
    json.dump(manifest,f,indent=2);f.write("\n")
summary={"status":"PASS","phys_addr":hex(base),"addr_range":size,"setup_slack_ns":setup,"hold_slack_ns":hold,"files":files}
print(json.dumps(summary,indent=2))
