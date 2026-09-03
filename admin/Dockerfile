# App quản lý khách mời. Build context là gốc repo vì cần index.html làm
# template render thiệp riêng — giữ một nguồn template duy nhất.
FROM node:22-alpine

WORKDIR /app

COPY admin/package.json ./package.json
COPY admin/server.js ./server.js
COPY admin/public ./public

# Template thiệp + danh sách khách ban đầu (chỉ dùng để nạp lần đầu khi
# /data/guests.json chưa tồn tại).
COPY index.html ./invitation.html
COPY guests.csv ./guests.csv

ENV NODE_ENV=production
ENV PORT=3000
ENV DATA_DIR=/data

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1:3000/health || exit 1

CMD ["node", "server.js"]
