# Thiệp cưới là site tĩnh thuần — chỉ cần nginx phục vụ file.
#
# Thiệp riêng của từng khách (/t/<mã>) KHÔNG sinh ở đây nữa: app quanly render
# tại runtime từ danh sách khách của nó, và Traefik trỏ doantien.love/t/* sang
# đó. Để cả hai nơi cùng sinh sẽ thành hai nguồn dữ liệu lệch nhau.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css script.js site.webmanifest robots.txt /usr/share/nginx/html/
COPY assets /usr/share/nginx/html/assets

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/health || exit 1
