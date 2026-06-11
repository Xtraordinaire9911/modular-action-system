# Eclipse Thingweb node-wot servient for the smart-room IoT Things.
FROM node:20-slim

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY server.js ./

EXPOSE 8080 8081
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD node -e "require('http').get('http://localhost:8080/thermostat', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
