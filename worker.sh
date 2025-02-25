echo "system up"
set -a
source .env
exec python3 ./main.py &
exec python3 ./app.py