FROM uselagoon/node-20

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy project files
COPY . .

# Build the application
RUN npm run build

# Expose port 3000 for the server
EXPOSE 3000

ENV ESLINT_CACHE_DIR=/tmp/.eslintcache

RUN mkdir -p /app/node_modules/.cache && fix-permissions /app/node_modules/.cache

# Serve the production build
CMD ["npm", "run", "serve"]