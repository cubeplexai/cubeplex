#sudo docker buildx create --use --node base --name cubebuilder --driver-opt image=hub.sensedeal.vip/library/buildkit:buildx-stable-1
#sudo docker buildx build --platform=linux/amd64 -t hub.sensedeal.vip/library/cubebox-sandbox:24.04-20260309 --push .
sudo docker build -t hub.sensedeal.vip/library/cubebox-sandbox:24.04-20260310 --push .
