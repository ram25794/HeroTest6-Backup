import argparse
import subprocess
import time
import datetime
import re
import os
import signal
import sys
import threading

# --- Shared State for Threads ---
# A lock is needed to safely update and read the time sum from different threads
g_lock = threading.Lock()
g_bulker_time_sum = 0.0
g_eni_index = 1
g_process = None # Global process handle for cleanup

def log_monitor_worker(ram_log_file, took_time_re):
    """
    This function runs in a background thread. Its ONLY job is to watch syslog,
    write to the detailed log, and update the shared bulker_time_sum.
    """
    global g_bulker_time_sum, g_eni_index, g_process

    g_process = subprocess.Popen(
        ["tail", "-F", "/var/log/syslog"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )
    
    for line in g_process.stdout:
        if "Ram Test" in line:
            with g_lock:
                # Use the global eni_index for tagging
                tagged_line = f"[ENI {g_eni_index}] {line.strip()}"
                write_log(ram_log_file, tagged_line + "\n")
                
                # Extract and sum "took" times
                m = took_time_re.search(line)
                if m:
                    try:
                        g_bulker_time_sum += float(m.group(1))
                    except (ValueError, IndexError):
                        pass

def handle_sigint(sig, frame):
    """Handles Ctrl+C for a graceful exit."""
    print("\n[INFO] Ctrl+C received. Exiting...")
    if g_process and g_process.poll() is None:
        g_process.terminate()
    sys.exit(0)

def get_crm_counts():
    """Fetches current CRM counters from the SONiC database."""
    try:
        crm_routes = int(subprocess.check_output(
            "sonic-db-cli COUNTERS_DB HGET 'CRM:STATS' 'crm_stats_dash_ipv4_outbound_routing_used'",
            shell=True, stderr=subprocess.DEVNULL).decode().strip())
        crm_mappings = int(subprocess.check_output(
            "sonic-db-cli COUNTERS_DB HGET 'CRM:STATS' 'crm_stats_dash_ipv4_outbound_ca_to_pa_used'",
            shell=True, stderr=subprocess.DEVNULL).decode().strip())
        return crm_routes, crm_mappings
    except Exception:
        # In a polling script, we expect some failures, so keep this quiet
        return None, None

def write_log(file_path, line):
    """Appends a line of text to a specified log file."""
    with open(file_path, "a") as f:
        f.write(line)

def main():
    """Main function to run the monitoring process."""
    global g_bulker_time_sum, g_eni_index

    parser = argparse.ArgumentParser(
        description="Polls CRM counters to monitor ENI processing on a SONiC device.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-r', '--routes', type=int, default=100000, help="Routes per ENI.")
    parser.add_argument('-m', '--mappings', type=int, default=125000, help="Mappings per ENI.")
    parser.add_argument('-t', '--total-enis', type=int, default=64, help="Total ENIs to monitor.")
    parser.add_argument('--poll-interval', type=float, default=1.0, help="Seconds between counter checks.")
    args = parser.parse_args()

    # --- Setup ---
    signal.signal(signal.SIGINT, handle_sigint)
    ram_log_file = "ram_test.log"
    summary_log_file = "eni_summary.log"
    took_time_re = re.compile(r"took ([0-9.]+) seconds$")
    
    for f in [ram_log_file, summary_log_file]:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass

    print(f"[INFO] Script started. Polling every {args.poll_interval} second(s).")
    print(f"[INFO] Monitoring for {args.total_enis} ENIs...")
    print(f"[INFO] Criteria per ENI: {args.routes} routes, {args.mappings} mappings.")

    # --- Start background thread for log monitoring ---
    log_thread = threading.Thread(
        target=log_monitor_worker,
        args=(ram_log_file, took_time_re),
        daemon=True  # Allows main script to exit even if this thread is blocked
    )
    log_thread.start()
    print("[INFO] Background log monitor started.")

    # --- Main Polling Loop ---
    initial_routes_base, initial_mappings_base = get_crm_counts()
    if initial_routes_base is None:
        print("[ERROR] Could not get initial CRM counts. Exiting.")
        sys.exit(1)
    
    print(f"[INFO] Initial baseline counts read: Routes={initial_routes_base}, Mappings={initial_mappings_base}")
    eni_start_time = time.time()

    while g_eni_index <= args.total_enis:
        total_crm_routes, total_crm_mappings = get_crm_counts()
        if total_crm_routes is None:
            time.sleep(args.poll_interval)
            continue # If DB read fails, just wait and try again

        routes_delta_total = total_crm_routes - initial_routes_base
        mappings_delta_total = total_crm_mappings - initial_mappings_base
        expected_routes_cumulative = args.routes * g_eni_index
        expected_mappings_cumulative = args.mappings * g_eni_index

        if routes_delta_total >= expected_routes_cumulative and mappings_delta_total >= expected_mappings_cumulative:
            duration = time.time() - eni_start_time
            
            with g_lock:
                # Safely read and then reset the shared time sum
                current_bulker_sum = g_bulker_time_sum
                g_bulker_time_sum = 0.0

            # Log completion
            summary_line = f"ENI {g_eni_index} COMPLETED {duration:.2f} {current_bulker_sum:.3f}\n"
            write_log(summary_log_file, summary_line)
            print(f"\n[SUCCESS] ENI {g_eni_index} completed. Processing Time: {duration:.2f}s | Bulk 'took' Time: {current_bulker_sum:.3f}s")

            # Move to next ENI
            g_eni_index += 1
            if g_eni_index <= args.total_enis:
                print(f"[INFO] Now monitoring for ENI {g_eni_index}...")
                eni_start_time = time.time() # Reset timer for the new ENI

        time.sleep(args.poll_interval)

    print("\n[INFO] All ENIs processed. Script finished.")
    if g_process and g_process.poll() is None:
        g_process.terminate()

if __name__ == "__main__":
    main()
