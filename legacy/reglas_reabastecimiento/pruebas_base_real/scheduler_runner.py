#!/usr/bin/env python3
"""
Scheduler runner que permanece en ejecución y ejecuta `replenishment_update_auto.py`
cada sábado a las 13:30 (server timezone). Pensado para lanzarse con pm2:

pm2 start scheduler_runner.py --interpreter python3 --name replenishment-scheduler

El runner: calcula el próximo sábado 13:30, duerme hasta entonces, ejecuta el script
y repite. Guarda salida en stdout/stderr (pm2 la captura en logs).
"""
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(__file__)
PY_SCRIPT = os.path.join(BASE_DIR, 'replenishment_update_auto.py')

LOG_PREFIX = os.path.join(BASE_DIR, 'logs', 'scheduler')


def next_saturday_0130(now=None):
    now = now or datetime.now()
    # weekday(): Monday=0 ... Saturday=5
    days_ahead = (5 - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(hour=13, minute=30, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=7)
    return target


def run_job():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_stdout = f"{LOG_PREFIX}_{timestamp}.out.log"
    log_stderr = f"{LOG_PREFIX}_{timestamp}.err.log"
    with open(log_stdout, 'ab') as out, open(log_stderr, 'ab') as err:
        try:
            proc = subprocess.Popen([sys.executable, PY_SCRIPT], stdout=out, stderr=err, cwd=BASE_DIR)
            ret = proc.wait()
            return ret
        except Exception as e:
            err.write(str(e).encode('utf-8'))
            return 1


def ensure_logs_dir():
    logs_dir = os.path.join(BASE_DIR, 'logs')
    os.makedirs(logs_dir, exist_ok=True)


def main():
    ensure_logs_dir()
    while True:
        now = datetime.now()
        target = next_saturday_0130(now)
        sleep_seconds = (target - now).total_seconds()
        # Safety: if negative or too small, skip
        if sleep_seconds <= 0:
            sleep_seconds = 60
        # Sleep until next scheduled run
        time.sleep(sleep_seconds)
        # Run the job
        run_job()
        # After running, loop to compute next run


if __name__ == '__main__':
    main()
