module.exports = {
  apps: [{
    name: 'prauto-scraper',
    script: 'main.py',
    interpreter: 'python3',
    interpreter_args: '-u',
    instances: 1,
    exec_mode: 'fork',
    env: {
      HEADLESS: 'true',
      PYTHONPATH: '.',
      PYTHONUNBUFFERED: '1'
    },
    env_production: {
      NODE_ENV: 'production',
      HEADLESS: 'true'
    },
    watch: false,
    max_memory_restart: '1G',
    max_restarts: 10,
    min_uptime: '10s',
    error_file: './logs/err.log',
    out_file: './logs/out.log',
    log_file: './logs/combined.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: true,
    time: true,
    cron_restart: '0 */4 * * *', // Ejecutar cada 4 horas
    restart_delay: 5000
  }],

  // Configuración para ejecución única
  deploy: {
    production: {
      user: 'ubuntu',
      host: 'localhost',
      ref: 'origin/main',
      repo: 'git@github.com:tustage3/stage3-performance-lube.git',
      path: '/home/ubuntu/stage3-performance-lube',
      'pre-setup': 'sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv python3-dev chromium-browser',
      'post-deploy': 'pip3 install -r requirements.txt && pm2 reload ecosystem.config.js --env production'
    }
  }
};