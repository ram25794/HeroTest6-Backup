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

# Argument parsing
GENERATE_CONFIGS = False
args = sys.argv[1:]
if '--generate-configs' in args:
    GENERATE_CONFIGS = True
    args.remove('--generate-configs')
if len(args) != 5:
    print("Usage: python3 generate_configs.py <NUM_OUTBOUND_ROUTES_PER_ENI> <NUM_VNET_MAPPINGS_PER_ENI> <NUM_ENIS> <DPU_NUMBER> <HOSTNAME> [--generate-configs]")
    sys.exit(1)

NUM_OUTBOUND_ROUTES_PER_ENI = int(args[0])
NUM_VNET_MAPPINGS_PER_ENI = int(args[1])
NUM_ENIS = int(args[2])
DPU_NUMBER = int(args[3])
HOSTNAME = args[4]

# Remove previously generated config files and shell script
if GENERATE_CONFIGS:
    if os.path.exists(INITIAL_OUTPUT_DIR):
        shutil.rmtree(INITIAL_OUTPUT_DIR)
    if os.path.exists(VNET_OUTPUT_DIR):
        shutil.rmtree(VNET_OUTPUT_DIR)
    os.makedirs(INITIAL_OUTPUT_DIR, exist_ok=True)
    os.makedirs(VNET_OUTPUT_DIR, exist_ok=True)
if os.path.exists('apply_configs.sh'):
    os.remove('apply_configs.sh')

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
    # Use only 1-254 for each octet to avoid invalid/broadcast IPs
    ip_second_octet = 1 + ((route_id // (254 * 254)) % 254)  # 1-254
    ip_third_octet = 1 + ((route_id // 254) % 254)           # 1-254
    ip_last_octet = 1 + (route_id % 254)                     # 1-254
    if ip_second_octet > 254 or ip_third_octet > 254 or ip_last_octet > 254:
        return None
    return {
        f"DASH_ROUTE_TABLE:group_id_eni{eni_id}:13.{ip_second_octet}.{ip_third_octet}.{ip_last_octet}/32": {
            "action_type": "vnet",
            "vnet": f"Vnet{vnet_id}"
        },
        "OP": "SET"
    }

def generate_vnet_mapping_table(mapping_id, vnet_id):
    # Use only 1-254 for each octet to avoid invalid/broadcast IPs
    ip_second_octet = 1 + ((mapping_id // (254 * 254)) % 254)  # 1-254
    ip_third_octet = 1 + ((mapping_id // 254) % 254)           # 1-254
    ip_last_octet = 1 + (mapping_id % 254)                     # 1-254
    if ip_second_octet > 254 or ip_third_octet > 254 or ip_last_octet > 254:
        return None
    # For underlay IPs, after reaching the max, use a single common IP
    max_combinations = min(254 * 254 * 254, MAX_UNDERLAY_IP_COMBINATIONS // NUM_ENIS)
    if mapping_id < max_combinations:
        underlay_ip = f"10.{ip_second_octet}.{ip_third_octet}.{ip_last_octet}"
    else:
        underlay_ip = "11.254.254.254"
    return {
        f"DASH_VNET_MAPPING_TABLE:Vnet{vnet_id}:13.{ip_second_octet}.{ip_third_octet}.{ip_last_octet}": {
            "routing_type": "privatelink",
            "underlay_ip": underlay_ip
        },
        "OP": "SET"
    }

if GENERATE_CONFIGS:
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
        route_configs = [generate_route_table (i, eni_id, vnet_id) for i in range(NUM_OUTBOUND_ROUTES_PER_ENI)]
        route_configs = [c for c in route_configs if c]
        mapping_configs = [generate_vnet_mapping_table(i, vnet_id) for i in range(NUM_VNET_MAPPINGS_PER_ENI)]
        mapping_configs = [c for c in mapping_configs if c]
        combined_configs = route_configs + mapping_configs
        with open(os.path.join(INITIAL_OUTPUT_DIR, f'eni_{eni_id}_combined.json'), 'w') as f:
            json.dump(combined_configs, f, indent=2)

# Generate the shell script to apply the configs
with open('apply_configs.sh', 'w') as f:
    f.write('''#!/bin/bash

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
DPU_IP="169.254.200.{dpu_ip_last_octet}"
DPU_SSH_PORT=$((5021 + {DPU_NUMBER}))

DPU_COMMAND_ROUTES='sonic-db-cli COUNTERS_DB HGET "CRM:STATS" "crm_stats_dash_ipv4_outbound_routing_used"'
DPU_COMMAND_MAPPINGS='sonic-db-cli COUNTERS_DB HGET "CRM:STATS" "crm_stats_dash_ipv4_outbound_ca_to_pa_used"'

trap 'echo "Interrupt received, stopping..."; exit 1' INT

echo "CRM Apply Timings" > "$CRM_LOG"
echo "ENI_ID,ROUTES_EXPECTED,ROUTES_APPLIED_TIME_SEC,MAPPINGS_EXPECTED,MAPPINGS_APPLIED_TIME_SEC,TOTAL_TIME_SEC" >> "$CRM_LOG"

echo "Applying initial configuration..."
./gnmi-configurator --host "$HOST" --dpu "$DPU" --port "$PORT" --json "$INITIAL_CONFIG_DIR/config_part_1.json" --chunksize "$CHUNKSIZE"

#check_route_mappings() {{
#    local expected_value=$1
#    local cmd="sshpass -p '$PASSWORD' ssh -T -n -o LogLevel=ERROR -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{DPU_USER}}@${{HOST}} \"$DPU_COMMAND_ROUTES\""
#    OUTPUT=$(sshpass -p "$PASSWORD" ssh -T -n -o LogLevel=ERROR -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{HOST_USER}}@${{HOST}} "$cmd" 2>&1 | tr -d '\r')
#    echo "[CRM ROUTES] Received: $OUTPUT, Expected: $expected_value"
#    if [ "$OUTPUT" == "$expected_value" ]; then
#        return 0
#    else
#        return 1
#    fi
#}}

#check_ca2pa_mappings() {{
#    local expected_value=$1
#    local cmd="sshpass -p '$PASSWORD' ssh -T -n -o LogLevel=ERROR -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{DPU_USER}}@${{HOST}} \"$DPU_COMMAND_MAPPINGS\""
#    OUTPUT=$(sshpass -p "$PASSWORD" ssh -T -n -o LogLevel=ERROR -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{HOST_USER}}@${{HOST}} "$cmd" 2>&1 | tr -d '\r')
#    echo "[CRM MAPPINGS] Received: $OUTPUT, Expected: $expected_value"
#    if [ "$OUTPUT" == "$expected_value" ]; then
#        return 0
#    else
#        return 1
#    fi
#}}

#run_on_dpu() {{
#    local cmd="$1"
#    sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{HOST_USER}}@${{HOST}} \
#        "sshpass -p '$PASSWORD' ssh -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{DPU_USER}}@${{HOST}} \"$cmd\"" 2>/dev/null | tr -d '\r'
#}}

echo "Applying per-ENI configs..."
for eni_id in $(seq 1 {NUM_ENIS}); do
    config_file="$INITIAL_CONFIG_DIR/eni_${{eni_id}}_combined.json"
    echo "Applying $config_file..."
    ./gnmi-configurator --host "$HOST" --dpu "$DPU" --port "$PORT" --json "$config_file" --chunksize "$CHUNKSIZE"
    while true; do
        log_found=$(sshpass -p "$PASSWORD" ssh -T -n -p $DPU_SSH_PORT -o LogLevel=ERROR -o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PreferredAuthentications=password ${{DPU_USER}}@${{HOST}} "grep -E 'ENI ${{eni_id}} COMPLETED.*' /home/admin/eni_summary.log")
        if [[ -n "$log_found" ]]; then
            echo "ENI $eni_id COMPLETED log found on DPU."
            break
        fi
        echo "Waiting for ENI $eni_id COMPLETED log on DPU..."
        sleep 5
    done
done

echo "All configurations applied successfully."
'''.format(
        HOSTNAME=HOSTNAME,
        DPU_NUMBER=DPU_NUMBER,
        NUM_ENIS=NUM_ENIS,
        NUM_OUTBOUND_ROUTES_PER_ENI=NUM_OUTBOUND_ROUTES_PER_ENI,
        NUM_VNET_MAPPINGS_PER_ENI=NUM_VNET_MAPPINGS_PER_ENI,
        dpu_ip_last_octet=DPU_NUMBER + 1
    ))
os.chmod('apply_configs.sh', 0o755)
print("Generated 'apply_configs.sh' to apply the configurations and log CRM timing.")
