import pynetbox
import yaml
import ipaddress
from pprint import pprint

nb = pynetbox.api('http://192.168.8.10:8080', token='wu4VAC2xlNwk2aAtSzF2KFODDrFVSSzaOf0mUveG')


# get ASNs from NetBox
leaf_asn = nb.ipam.asns.get(description="LEAF_ASN").asn
spine_asn = nb.ipam.asns.get(description="SPINE_ASN").asn
leaf_vteps = []
spine_vteps = []

print(f"Leaf ASN: {leaf_asn}")
print(f"Spine ASN: {spine_asn}")

# get all devices
devices = list(nb.dcim.devices.all())
# for device in devices:
#     print(device.name, device.role.slug)
#     interfaces = nb.dcim.interfaces.filter(device_id=device.id)
#     for interface in interfaces:
#         ips = list(nb.ipam.ip_addresses.filter(interface_id=interface.id))
#         if ips:  # only print interfaces that have IPs
#             print(f"  {interface.name}: {ips[0].address}")

for device in devices:
    interfaces = nb.dcim.interfaces.filter(device_id=device.id)
    for interface in interfaces:
        ips = list(nb.ipam.ip_addresses.filter(interface_id=interface.id))
        if ips:  # only include interfaces that have IPs
            ip = ips[0].address
            if device.role.slug == 'leaf-switch' and interface.name == 'lo0':
                leaf_vteps.append(ip.split('/')[0])  # store only the IP without subnet mask
            elif device.role.slug == 'spine-switch' and interface.name == 'lo0':
                spine_vteps.append(ip.split('/')[0])  # store only the IP without subnet mask

for device in devices:
    d = {}
    d['hostname'] = device.name
    d['interfaces'] = []
    d['ospf_neighbors'] = []
    d['evpn_neighbors'] = []

    if device.role.slug == 'leaf-switch':
        d['asn'] = leaf_asn
        remote_asn = spine_asn
        d['evpn_neighbors'] = spine_vteps
        device_num = int(''.join(filter(str.isdigit, device.name.split('-')[-1])))
        d['mlag_side'] = 'left' if device_num % 2 == 1 else 'right'
        d['remote_as'] = spine_asn
    elif device.role.slug == 'spine-switch':
        d['asn'] = spine_asn
        remote_asn = leaf_asn
        d['evpn_neighbors'] = leaf_vteps
        d['remote_as'] = leaf_asn

    interfaces = nb.dcim.interfaces.filter(device_id=device.id)
    for interface in interfaces:
        ips = list(nb.ipam.ip_addresses.filter(interface_id=interface.id))
        if ips:  # only include interfaces that have IPs
            ip = ips[0].address
            ip_only = ip.split('/')[0]  # remove subnet mask
            mask_only = ip.split('/')[1]  # get subnet mask
            if interface.name == 'lo0':
                d['loopback0_ip'] = ip_only
            elif interface.name == 'lo1':
                d['loopback1_ip'] = ip_only
            elif interface.name == 'mgmt':
                d['ansible_host'] = ip_only
            elif 'ethernet' in interface.name.lower():
                d['interfaces'].append({
                    'interface': interface.name,
                    'ip': ip_only, 
                    'mask': '/' + mask_only
                })
                network = ipaddress.ip_interface(ip).network
                hosts = list(network.hosts())
                neighbor_ip = str(hosts[0]) if ip_only == str(hosts[1]) else str(hosts[1])
                d['ospf_neighbors'].append({
                    'neighbor': neighbor_ip,
                    'state': 'present'
                })
    pprint(d)
    print()

    filename = f"host_vars/{device.name}.yaml"
    with open(filename, 'w') as f:
        yaml.dump(d, f, default_flow_style=False)
    print(f"Generated {filename}")

inventory = {
    'all': {
        'children': {
            'eos': {
                'children': {
                    'leafs': { 'hosts': {} },
                    'spines': { 'hosts': {} }
                }
            }
        }
    }
}

for device in devices:
    if device.role.slug == 'leaf-switch':
        inventory['all']['children']['eos']['children']['leafs']['hosts'][device.name] = None
    elif device.role.slug == 'spine-switch':
        inventory['all']['children']['eos']['children']['spines']['hosts'][device.name] = None

with open('hosts.yaml', 'w') as f:
    yaml.dump(inventory, f, default_flow_style=False)

vrfs = list(nb.ipam.vrfs.all())
vrf_list = []
for vrf in vrfs:

    vrf_prefixes = list(nb.ipam.prefixes.filter(vrf_id=vrf.id))
    vlan_list = []
    for prefix in vrf_prefixes:
        if prefix.vlan:
            network = ipaddress.ip_network(prefix.prefix)
            gw_ip = str(list(network.hosts())[0])
            mask = str(network.prefixlen)

            vlan_list.append({
                'vrf': prefix.vrf.name if prefix.vrf else None,
                'vlan': prefix.vlan.vid if prefix.vlan else None,
                'prefix': prefix.prefix,
                'gw_ip': gw_ip,
                'mask': mask, 
            })

    vrf_list.append({
        'name': vrf.name,
        'rd': vrf.rd,
        'import_rt': str(vrf.import_targets[0]) if vrf.import_targets else None,
        'export_rt': str(vrf.export_targets[0]) if vrf.export_targets else None,
        'l3vni': vrf.custom_fields.get('L3VNI')
        'vlans': vlan_list
    })

# build group_vars data
leafs_group_vars = {
    'vrfs': vrf_list
}

# write to group_vars/leafs.yml
with open('group_vars/leafs.yml', 'w') as f:
    yaml.dump(leafs_group_vars, f, default_flow_style=False)
print("Generated group_vars/leafs.yml")    