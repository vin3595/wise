FROM python:3.12-alpine
WORKDIR /app
COPY wise_print_bridge.py .
# On the Wise Box the bridge talks to the API over the internal network and to printers on the shop LAN.
CMD ["sh", "-c", "if [ -z \"$WISE_PRINT_KEY\" ]; then echo 'No print key yet: run  sudo wise print-key <key>'; while true; do sleep 3600; done; fi; exec python -u wise_print_bridge.py --server \"$WISE_SERVER\" --key \"$WISE_PRINT_KEY\""]
