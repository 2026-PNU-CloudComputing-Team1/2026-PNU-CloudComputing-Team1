FROM nginx:alpine

# tc(netem)로 인공 지연을 주려면 iproute2가, entrypoint.sh의 envsubst는 gettext가 필요
# busybox의 ip는 tc를 포함하지 않으므로 별도 설치
RUN apk add --no-cache iproute2 gettext

COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# proxy_cache가 사용할 디렉터리 (nginx.conf의 proxy_cache_path와 일치)
RUN mkdir -p /var/cache/nginx/edge

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
