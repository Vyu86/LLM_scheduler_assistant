sudo apt update && sudo apt upgrade -y

# Download and install docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install python venv
sudo apt install python3.13-venv -y

# Install golang
sudo apt install golang-go -y

# Install kind
go install sigs.k8s.io/kind@v0.31.0
echo "export PATH=$PATH:$(go env GOPATH)/bin" >> ~/.bashrc

# Install kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

source $HOME/.profile

# create python venv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
