# Sinh trang thiệp riêng cho từng khách từ guests.csv.
# Làm ở build stage để repo không phải chứa hàng trăm file HTML gần giống nhau.
FROM python:3.12-alpine AS guests
WORKDIR /src
COPY index.html guests.csv ./
COPY tools/guests.py ./tools/
RUN python3 tools/guests.py build /out

# Thiệp cưới là site tĩnh thuần — chỉ cần nginx phục vụ file.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css script.js site.webmanifest robots.txt /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets
COPY --from=guests /out /usr/share/nginx/html/t

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/health || exit 1
