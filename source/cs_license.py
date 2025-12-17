import grpc
import api_gateway_pb2
import api_gateway_pb2_grpc
import time
from license import license

lic = license()

try:            
    while True:
        lic.read_lic_data()
except KeyboardInterrupt:
    print("\nStopped by user.")
finally:
    print("Finish...")



  
#raise SystemExit 
