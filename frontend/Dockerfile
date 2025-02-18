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

RUN mkdir -p /app/node_modules/.cache && fix-permissions /app/node_modules/.cache

# Create script to generate runtime config which will be loaded by Lagoon Entrypoints
RUN echo '#!/bin/sh\n\
sed "s|\${REACT_APP_API_URL}|$REACT_APP_API_URL|g" /app/build/config.js > /app/build/config.tmp.js\n\
mv /app/build/config.tmp.js /app/build/config.js\n\
exec "$@"' > /lagoon/entrypoints/80-nodejs-config.sh

CMD ["npm", "run", "serve"]