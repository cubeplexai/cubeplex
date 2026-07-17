#sudo docker buildx create --use --node base --name cubebuilder --driver-opt image=hub.sensedeal.vip/library/buildkit:buildx-stable-1
#sudo docker buildx build --platform=linux/amd64 -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260309 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260311 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260514 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260521 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260525 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260531 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260603 --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260603b --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260603c --push .
#sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260603d --push .
sudo docker build -t hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260623-build --push .
