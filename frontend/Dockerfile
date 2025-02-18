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

RUN mkdir -p /app/node_modules/.cache \
    && fix-permissions /app/node_modules/.cache \
    && fix-permissions /app/build/config.js

# Copy the Lagoon entrypoint script
COPY lagoon/entrypoints/80-nodejs-config.sh /lagoon/entrypoints/80-nodejs-config.sh

CMD ["npm", "run", "serve"]