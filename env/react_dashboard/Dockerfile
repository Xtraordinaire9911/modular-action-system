# React smart-room dashboard (Vite preview server for the demo).
FROM node:20-slim

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . ./
RUN npm run build

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD node -e "require('http').get('http://localhost:3000', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["npm", "run", "preview"]
