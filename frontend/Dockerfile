FROM uselagoon/node-20

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy project files
COPY . .

# Expose port 3000 for development server
EXPOSE 3000

ENV ESLINT_CACHE_DIR=/tmp/.eslintcache

RUN mkdir -p /app/node_modules/.cache && fix-permissions /app/node_modules/.cache

# Start development server
CMD ["npm", "start"]