FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy project files
COPY . .

# Expose port 3000 for development server
EXPOSE 3000

# Start development server
CMD ["npm", "start"]