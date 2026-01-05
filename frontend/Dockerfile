FROM uselagoon/node-22:25.12.1@sha256:2988e452b032e33b98fb5102ae6685b3bbbf0bb3f1f24fdf6dc5f87198b32a7a AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy the rest of the application
COPY . .

# Build the Next.js application
RUN npm run build

# Production stage
FROM uselagoon/node-22:25.12.1@sha256:2988e452b032e33b98fb5102ae6685b3bbbf0bb3f1f24fdf6dc5f87198b32a7a

WORKDIR /app

# Copy package files and install production dependencies
COPY package*.json ./
RUN npm install --omit-dev

# Copy built application from builder
COPY --from=builder /app/.lagoon.env ./.lagoon.env
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/src ./src

EXPOSE 3000

CMD ["npm", "start"]
