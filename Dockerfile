# Thiệp cưới là site tĩnh thuần — chỉ cần nginx phục vụ file.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css script.js /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/health || exit 1
