import json
import os
import sys
import uuid
import shutil

# Constants
NUM_VNETS = 1024
MAX_UNDERLAY_IP_COMBINATIONS = 1048575
INITIAL_OUTPUT_DIR = 'split_configs'
VNET_OUTPUT_DIR = 'vnet_mappings'

if len(sys.argv) != 6:
    print("Usage: python3 generate_configs.py <NUM_OUTBOUND_ROUTES_PER_ENI> <NUM_VNET_MAPPINGS_PER_ENI> <NUM_ENIS> <DPU_NUMBER> <HOSTNAME>")
    sys.exit(1)

NUM_OUTBOUND_ROUTES_PER_ENI = int(sys.argv[1])
NUM_VNET_MAPPINGS_PER_ENI = int(sys.argv[2])
NUM_ENIS = int(sys.argv[3])
DPU_NUMBER = int(sys.argv[4])
HOSTNAME = sys.argv[5]

# Remove previously generated config files and shell script
if os.path.exists(INITIAL_OUTPUT_DIR):
    shutil.rmtree(INITIAL_OUTPUT_DIR)
if os.path.exists(VNET_OUTPUT_DIR):
    shutil.rmtree(VNET_OUTPUT_DIR)
if os.path.exists('apply_configs.sh'):
    os.remove('apply_configs.sh')
os.makedirs(INITIAL_OUTPUT_DIR, exist_ok=True)
os.makedirs(VNET_OUTPUT_DIR, exist_ok=True)

def generate_guid():
    return str(uuid.uuid4())

def generate_routing_type_table():
    return [
        {
            "DASH_ROUTING_TYPE_TABLE:privatelink": {
                "items": [
                    {"action_name": "action1", "action_type": "4_to_6"},
                    {"action_name": "action2", "action_type": "staticencap", "encap_type": "nvgre", "vni": 300}
                ]
            },
            "OP": "SET"
        }
    ]

def generate_vnet_table(vnet_id):
    vni = 5000 + vnet_id
    return {
        f"DASH_VNET_TABLE:Vnet{vnet_id}": {
            "vni": str(vni),
            "guid": generate_guid()
        },
        "OP": "SET"
    }

def generate_appliance_table():
    return [
        {
            "DASH_APPLIANCE_TABLE:22": {
                "sip": "10.201.0.10",
                "vm_vni": "101"
            },
            "OP": "SET"
        }
    ]

def generate_eni_table(eni_id, vnet_id):
    mac_address = f"00:00:00:00:{eni_id:02x}:{eni_id:02x}"
    return {
        f"DASH_ENI_TABLE:eni{eni_id}": {
            "mac_address": mac_address,
            "underlay_ip": f"13.132.111.{eni_id % 256}",
            "admin_state": "enabled",
            "vnet": f"Vnet{vnet_id}",
            "pl_underlay_sip": "10.201.0.10",
            "pl_sip_encoding": f"0:0:0:2000:111:{eni_id:02x}::/::ffff:ffff:0:0"
        },
        "OP": "SET"
    }

def generate_route_group_table(eni_id):
    return {
        f"DASH_ROUTE_GROUP_TABLE:group_id_eni{eni_id}": {
            "guid": generate_guid(),
            "version": "1"
        },
        "OP": "SET"
    }

def generate_route_table(route_id, eni_id, vnet_id):
    ip_second_octet = (route_id // 256) % 256
    ip_third_octet = (route_id // (256 * 256)) % 256
    ip_last_octet = route_id % 256
    if ip_second_octet > 255 or ip_third_octet > 255 or ip_last_octet > 255:
        return None
    return {
        f"DASH_ROUTE_TABLE:group_id_eni{eni_id}:13.{ip_second_octet}.{ip_third_octet}.{ip_last_octet}/32": {
            "action_type": "vnet",
            "vnet": f"Vnet{vnet_id}"
        },
        "OP": "SET"
    }

def generate_vnet_mapping_table(mapping_id, vnet_id):
    ip_second_octet = (mapping_id // 256) % 256
    ip_third_octet = (mapping_id // (256 * 256)) % 256
    ip_last_octet = mapping_id % 256
    if ip_second_octet > 255 or ip_third_octet > 255 or ip_last_octet > 255:
        return None
    underlay_ip_id = mapping_id % MAX_UNDERLAY_IP_COMBINATIONS
    underlay_ip_second_octet = (underlay_ip_id // 256) % 256
    underlay_ip_third_octet = (underlay_ip_id // (256 * 256)) % 256
    underlay_ip_last_octet = underlay_ip_id % 256
    underlay_ip = f"13.132.{underlay_ip_third_octet}.{underlay_ip_last_octet}"
    return {
        f"DASH_VNET_MAPPING_TABLE:Vnet{vnet_id}:13.{ip_second_octet}.{ip_third_octet}.{ip_last_octet}": {
            "routing_type": "privatelink",
            "underlay_ip": underlay_ip
        },
        "OP": "SET"
    }

# Generate initial config (routing type, appliance, all VNETs, all ENIs, all route groups)
initial_configs = []
initial_configs.extend(generate_routing_type_table())
initial_configs.extend(generate_appliance_table())
for vnet_id in range(1, NUM_VNETS + 1):
    initial_configs.append(generate_vnet_table(vnet_id))
for eni_id in range(1, NUM_ENIS + 1):
    vnet_id = eni_id if eni_id <= NUM_VNETS else ((eni_id - 1) % NUM_VNETS) + 1
    initial_configs.append(generate_eni_table(eni_id, vnet_id))
    initial_configs.append(generate_route_group_table(eni_id))
with open(os.path.join(INITIAL_OUTPUT_DIR, 'config_part_1.json'), 'w') as f:
    json.dump(initial_configs, f, indent=2)

# Generate per-ENI combined configs (routes + mappings)
for eni_id in range(1, NUM_ENIS + 1):
    vnet_id = eni_id if eni_id <= NUM_VNETS else ((eni_id - 1) % NUM_VNETS) + 1
    route_configs = [generate_route_table(i, eni_id, vnet_id) for i in range(NUM_OUTBOUND_ROUTES_PER_ENI)]
    route_configs = [c for c in route_configs if c]
    mapping_configs = [generate_vnet_mapping_table(i, vnet_id) for i in range(NUM_VNET_MAPPINGS_PER_ENI)]
    mapping_configs = [c for c in mapping_configs if c]
    combined_configs = route_configs + mapping_configs
    with open(os.path.join(INITIAL_OUTPUT_DIR, f'eni_{eni_id}_combined.json'), 'w') as f:
        json.dump(combined_configs, f, indent=2)

# Generate the shell script to apply the configs
with open('apply_configs.sh', 'w') as f:
    f.write(f'''#!/bin/bash

# Ensure sshpass is installed
if ! command -v sshpass &> /dev/null; then
    echo "sshpass could not be found, attempting to install..."
    if [ -f /etc/debian_version ]; then
        sudo apt-get update && sudo apt-get install -y sshpass
    elif [ -f /etc/redhat-release ]; then
        sudo yum install -y sshpass
    else
        echo "Please install sshpass manually. Exiting."
        exit 1
    fi
fi

HOST="{HOSTNAME}"
DPU="{DPU_NUMBER}"
PORT="8080"
INITIAL_CONFIG_DIR="split_configs"
CHUNKSIZE="25000"
CRM_LOG="crm_apply_timings.csv"
PASSWORD="YourPaSsWoRd"
HOST_USER="admin"
DPU_USER="admin"
DPU_IP="169.254.200.{DPU_NUMBER + 1}"

DPU_COMMAND_ROUTES='sonic-db-cli COUNTERS_DB HGET "CRM:STATS" "crm_stats_dash_ipv4_outbound_routing_used"'
DPU_COMMAND_MAPPINGS='sonic-db-cli COUNTERS_DB HGET "CRM:STATS" "crm_stats_dash_ipv4_outbound_ca_to_pa_used"'

trap 'echo "Interrupt received, stopping..."; exit 1' INT

echo "CRM Apply Timings" > "$CRM_LOG"
echo "ENI_ID,ROUTES_EXPECTED,ROUTES_APPLIED_TIME_SEC,MAPPINGS_EXPECTED,MAPPINGS_APPLIED_TIME_SEC" >> "$CRM_LOG"

echo "Applying initial configuration..."
./gnmi-configurator --host "$HOST" --dpu "$DPU" --port "$PORT" --json "$INITIAL_CONFIG_DIR/config_part_1.json" --chunksize "$CHUNKSIZE"

check_route_mappings() {{
    local expected_value=$1
    OUTPUT=$(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no ${{HOST_USER}}@${{HOST}} \
        "sshpass -p '$PASSWORD' ssh -o StrictHostKeyChecking=no ${{DPU_USER}}@${{DPU_IP}} \"$DPU_COMMAND_ROUTES\"" \
    2>/dev/null | tr -d '\r')
    echo "[CRM ROUTES] Received: $OUTPUT, Expected: $expected_value"
    if [ "$OUTPUT" == "$expected_value" ]; then
        return 0
    else
        return 1
    fi
}}

check_ca2pa_mappings() {{
    local expected_value=$1
    OUTPUT=$(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no ${{HOST_USER}}@${{HOST}} \
        "sshpass -p '$PASSWORD' ssh -o StrictHostKeyChecking=no ${{DPU_USER}}@${{DPU_IP}} \"$DPU_COMMAND_MAPPINGS\"" \
    2>/dev/null | tr -d '\r')
    echo "[CRM MAPPINGS] Received: $OUTPUT, Expected: $expected_value"
    if [ "$OUTPUT" == "$expected_value" ]; then
        return 0
    else
        return 1
    fi
}}

echo "Applying per-ENI configs and measuring timing..."
expected_routes=0
expected_mappings=0
total_all_eni_time=0
for eni_id in $(seq 1 {NUM_ENIS}); do
    config_file="$INITIAL_CONFIG_DIR/eni_${{eni_id}}_combined.json"
    echo "Applying $config_file..."
    start_time=$(date +%s)
    ./gnmi-configurator --host "$HOST" --dpu "$DPU" --port "$PORT" --json "$config_file" --chunksize "$CHUNKSIZE"
    expected_routes=$((expected_routes + {NUM_OUTBOUND_ROUTES_PER_ENI}))
    expected_mappings=$((expected_mappings + {NUM_VNET_MAPPINGS_PER_ENI}))
    while true; do
        route_ok=1
        mapping_ok=1
        check_route_mappings $expected_routes && route_ok=0
        check_ca2pa_mappings $expected_mappings && mapping_ok=0
        if [ $route_ok -eq 0 ] && [ $mapping_ok -eq 0 ]; then
            break
        fi
        echo "Waiting for both route and mapping counters to reach expected values for ENI $eni_id..."
        sleep 2
    done
    end_time=$(date +%s)
    total_time=$((end_time - start_time))
    total_all_eni_time=$((total_all_eni_time + total_time))
    echo "ENI $eni_id: Both counters reached expected values in $total_time seconds."
    echo "$eni_id,$expected_routes,$expected_routes,$expected_mappings,$expected_mappings,$total_time" >> "$CRM_LOG"
done

echo "Total time for all ENIs: $total_all_eni_time seconds."
echo "All configurations applied successfully. Timing results in $CRM_LOG."
''')
os.chmod('apply_configs.sh', 0o755)
print("Generated 'apply_configs.sh' to apply the configurations and log CRM timing.")
