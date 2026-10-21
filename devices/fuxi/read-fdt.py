"""Flattened device tree reader, cell-size aware.

Extracts the nodes a UniXNU board port needs from a running device's
/sys/firmware/fdt:

  root #address-cells / #size-cells
  every memory node, with reg decoded using the parent's cell sizes
  splash_region
  dsi / panel nodes
  a full node-path listing on request

No third-party modules. ASCII only.
"""
import json
import struct
import sys

FDT_MAGIC = 0xD00DFEED
BEGIN_NODE, END_NODE, PROP, NOP, END = 1, 2, 3, 4, 9


def parse(path):
    data = open(path, 'rb').read()
    (magic, totalsize, off_struct, off_strings, off_rsvmap,
     version, last_comp, boot_cpu, size_strings, size_struct) = struct.unpack_from('>10I', data, 0)
    if magic != FDT_MAGIC:
        raise SystemExit('not a device tree: magic 0x%08x' % magic)
    if totalsize != len(data):
        raise SystemExit('totalsize %d != file size %d' % (totalsize, len(data)))

    pos = off_struct
    path = []
    nodes = {}
    while True:
        token = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        if token == BEGIN_NODE:
            end = data.index(b'\x00', pos)
            name = data[pos:end].decode('ascii', 'replace')
            pos = (end + 4) & ~3
            path.append(name)
        elif token == END_NODE:
            if path:
                path.pop()
        elif token == PROP:
            length, nameoff = struct.unpack_from('>II', data, pos)
            pos += 8
            value = data[pos:pos + length]
            pos = (pos + length + 3) & ~3
            end = data.index(b'\x00', off_strings + nameoff)
            pname = data[off_strings + nameoff:end].decode('ascii', 'replace')
            key = '/' + '/'.join(p for p in path if p)
            nodes.setdefault(key, {})[pname] = value
        elif token == NOP:
            continue
        elif token == END:
            break
        else:
            raise SystemExit('bad token 0x%x at 0x%x' % (token, pos))
    return data, {'version': version, 'size': len(data)}, nodes


def u32(value):
    n = len(value) // 4
    return struct.unpack('>%dI' % n, value[:n * 4])


def decode_reg(nodes, key):
    """Decode reg using the parent node's #address-cells / #size-cells."""
    props = nodes.get(key, {})
    reg = props.get('reg')
    if reg is None:
        return []
    parent = key.rsplit('/', 1)[0] or '/'
    pp = nodes.get(parent, {})
    ac = u32(pp['#address-cells'])[0] if '#address-cells' in pp else 2
    sc = u32(pp['#size-cells'])[0] if '#size-cells' in pp else 1
    c = u32(reg)
    out = []
    step = ac + sc
    i = 0
    while i + step <= len(c):
        base = 0
        for j in range(ac):
            base = (base << 32) | c[i + j]
        size = 0
        for j in range(sc):
            size = (size << 32) | c[i + ac + j]
        out.append({'base': '0x%x' % base, 'size': '0x%x' % size})
        i += step
    return out


def main():
    dtb = sys.argv[1]
    list_all = len(sys.argv) > 2 and sys.argv[2] == '--list'
    _, header, nodes = parse(dtb)
    out = {'header': header}
    out['node_count'] = len(nodes)

    root = nodes.get('/', {})
    out['address_cells'] = u32(root['#address-cells'])[0] if '#address-cells' in root else None
    out['size_cells'] = u32(root['#size-cells'])[0] if '#size-cells' in root else None
    out['model'] = (root.get('model') or b'').rstrip(b'\x00').decode('ascii', 'replace')

    memories = {}
    for key in sorted(nodes):
        if key == '/memory' or key.startswith('/memory@'):
            memories[key] = decode_reg(nodes, key)
    out['memory'] = memories

    total = 0
    for regions in memories.values():
        for r in regions:
            total += int(r['size'], 16)
    out['memory_total_bytes'] = total
    out['memory_total_gib'] = round(total / (1 << 30), 2)

    splash = nodes.get('/reserved-memory/splash_region')
    if splash:
        out['splash_region'] = {
            'reg': decode_reg(nodes, '/reserved-memory/splash_region'),
            'label': (splash.get('label') or b'').rstrip(b'\x00').decode('ascii', 'replace'),
        }

    panels = []
    for key, props in nodes.items():
        low = key.lower()
        if 'panel' in low or 'dsi' in low:
            comp = props.get('compatible')
            panels.append({
                'path': key,
                'compatible': comp.rstrip(b'\x00').decode('ascii', 'replace').replace('\x00', ' ') if comp else None,
            })
    panels.sort(key=lambda p: p['path'])
    out['panel_and_dsi_nodes'] = panels
    out['panel_and_dsi_count'] = len(panels)

    if list_all:
        out['all_nodes'] = sorted(nodes)

    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
