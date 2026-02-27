module.exports = {
  apps : [{
    name: 'replenishment-scheduler',
    script: 'scheduler_runner.py',
    interpreter: 'python3',
    watch: false,
    autorestart: true,
    restart_delay: 1000,
    max_restarts: 10,
    // logs will be handled by pm2; scheduler_runner.py writes per-run logs too
  }]
};
