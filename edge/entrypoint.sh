#!/bin/sh
set -e

# docker-compose의 환경변수 DELAY_MS(ex: 10, 50, 80, 180)를 받아
# 컨테이너의 기본 인터페이스(eth0)에 tc netem으로 인공 지연을 적용한다.
# NET_ADMIN capability가 없으면 tc 명령이 실패하므로 docker-compose.yml에 cap_add 필요.

DELAY_MS="${DELAY_MS:-0}"
IFACE="${NET_IFACE:-eth0}"

if [ "$DELAY_MS" -gt 0 ] 2>/dev/null; then
    echo "[edge] applying netem delay=${DELAY_MS}ms on ${IFACE}"
    # 이미 qdisc가 있으면 change, 없으면 add — 컨테이너 재시작 양쪽 모두 동작
    if tc qdisc show dev "$IFACE" | grep -q "netem"; then
        tc qdisc change dev "$IFACE" root netem delay "${DELAY_MS}ms"
    else
        tc qdisc add    dev "$IFACE" root netem delay "${DELAY_MS}ms"
    fi
else
    echo "[edge] DELAY_MS not set or zero — skipping netem"
fi

# nginx.conf 안의 ${EDGE_DELAY_MS}는 envsubst로 치환해서 add_header에 노출
# (디버깅 편의용 — 응답 헤더 X-Edge-Delay-Ms로 확인 가능)
export EDGE_DELAY_MS="$DELAY_MS"
envsubst '${EDGE_DELAY_MS}' < /etc/nginx/nginx.conf > /tmp/nginx.conf
mv /tmp/nginx.conf /etc/nginx/nginx.conf

# nginx의 upstream 블록은 시작 시 1회만 DNS를 해석하므로,
# nginx-origin이 아직 안 뜬 상태에서 시작하면 즉시 죽는다.
# DNS가 해석될 때까지 최대 30초 대기.
ORIGIN_HOST="${ORIGIN_HOST:-nginx-origin}"
for i in $(seq 1 30); do
    if getent hosts "$ORIGIN_HOST" >/dev/null 2>&1; then
        echo "[edge] ${ORIGIN_HOST} resolved, starting nginx"
        break
    fi
    echo "[edge] waiting for ${ORIGIN_HOST} DNS... ($i/30)"
    sleep 1
done

echo "[edge] starting nginx on :8080"
exec nginx -g 'daemon off;'
