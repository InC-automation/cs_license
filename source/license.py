import grpc
import elecont_pb2, elecont_pb2_grpc
import api_gateway_pb2, api_gateway_pb2_grpc
from datetime import datetime, timezone, timedelta
import time
import configparser

class license:
    
    gw_connect_status = False
    uc_connect_status = False
    gw_channel = grpc.insecure_channel('localhost:8080')
    uc_channel = grpc.insecure_channel('localhost:29040')
    gw_stub = elecont_pb2_grpc.ElecontStub(gw_channel)
    uc_stub = elecont_pb2_grpc.ElecontStub(uc_channel)
    uc_url = ''
    gw_url = ''
    cycle_period = 10
    connect_period = 20
    time_delta = 0
    trace = False
    sig_list = []
    sig_dict = {}
    
    # инициализатор класса (читает параметры работы приложения)
    def __init__(self): 
        Config = configparser.ConfigParser()
        Config.read('settings.ini')
        self.uc_url = Config['Default']['USERCHANNEL']
        self.gw_url = Config['Default']['APIGATEWAY']
        self.trace = bool(int(Config['Default']['TRACE']))
        self.cycle_period = int(Config['Default']['CYCLE_PERIOD'])
        self.connect_period = int(Config['Default']['CONNECT_PERIOD'])
        self.time_delta = int(Config['Default']['TIME_DELTA'])
        
    # метод установливает соединение UserChannel и читает данные (сигналы)
    def uc_connect(self):
        if self.uc_connect_status: return
        print(f'{datetime.now().time()} UserChannel. Connect ({self.uc_url}) attempt...')
        self.uc_channel = grpc.insecure_channel(self.uc_url)
        self.uc_stub = elecont_pb2_grpc.ElecontStub(self.uc_channel)
        
        try:
            cs_data = self.uc_stub.GetAllObjectsData(elecont_pb2.Empty())
        except grpc.RpcError as e:
            print(f'{datetime.now().time()} UserChannel. GetAllObjectsData error: {e.code()}, {e.details()}')
            self.uc_close(self.connect_period)
            #return []
        else:
            if self.trace: print(f'{datetime.now().time()} UserChannel. Connect SUCCESS')
            self.uc_connect_status = True
            self.get_sig_list(cs_data)

    # метод установливает соединение ApiGateWay и читает данные (сигналы)
    def gw_connect(self):          
        if self.gw_connect_status: return
        print(f'{datetime.now().time()} ApiGateWay. Connect ({self.gw_url}) attempt...')
        self.gw_channel = grpc.insecure_channel(self.gw_url)
        self.gw_stub = api_gateway_pb2_grpc.ApiGatewayStub(self.gw_channel)
        
        try:
            cs_data = self.gw_stub.GetCsInfo(api_gateway_pb2.Empty())
        except grpc.RpcError as e:
            print(f'{datetime.now().time()} ApiGateWay. GetCsInfo error: {e.code()}, {e.details()}')
            self.gw_close(self.connect_period)
            #return []
        else:
            if self.trace: print(f'{datetime.now().time()} ApiGateWay. Connect SUCCESS')
            self.gw_connect_status = True

    # метод заполняет словари сигналов sig_dict и список сигналов sig_list
    # метод вызывается после установки соединения uc_connect
    def get_sig_list(self, cs_data):   
        #if self.trace: print(f'{datetime.now().time()} set_dicts...')
        self.sig_list.clear()
        self.sig_dict.clear()
        
        for obj in cs_data.data:
            if elecont_pb2.ObjectFamily.Value.Name(obj.family.value) == 'RX_SIGNAL':
                self.sig_list.append(obj.userdata)
                self.sig_dict[obj.userdata] = obj.guid

    # метод проверяет наличие соединений с КС. При отсутствии - пытается установить.
    def check_connection(self):
        if not self.gw_connect_status: 
            self.gw_connect()
        elif not self.uc_connect_status:
            self.uc_connect()
        return (self.gw_connect_status and self.uc_connect_status)
        
    # метод читает информацию о лицензии
    def read_lic_data(self):
        if self.check_connection():
            try:
                license_state = self.gw_stub.GetLicenseState(api_gateway_pb2.Empty())            
            except grpc.RpcError as e:
                print(f'{datetime.now().time()} ApiGateWay. GetLicenseState error: {e.code()}, {e.details()}')
                self.gw_close(self.connect_period)
            else:    
                key_id = self.get_key_number(license_state.key_id)
                self.set_signal('key_id', key_id)
                self.set_signal('key_presence', license_state.key_presence)
                self.set_signal('time_left', license_state.time_left)
                self.set_signal('key_presence_str', api_gateway_pb2.LicenseState.KeyState.Name(license_state.key_presence))
                self.set_signal('mode', license_state.mode)
                self.set_signal('mode_str', api_gateway_pb2.LicenseState.Mode.Name(license_state.mode))
                self.get_state()   # необходимо для поддержания соединения при редких передачах данных                
                time.sleep(self.cycle_period)

    # метод записывает значения сигнала в КС
    def set_signal(self, userdata, new_value):
        if userdata in self.sig_list:
            value = str(new_value)
            signal_guid = self.sig_dict[userdata]
            # print(f'{datetime.now().time()} signal: {userdata} guid: {signal.guid}')
            # if self.check_connection():
            try:
                signal = self.uc_stub.GetSignalByGuid(elecont_pb2.Guid(guid = signal_guid))
            except grpc.RpcError as e:
                print(f'{datetime.now().time()} UserChannel. GetSignalByGuid error: {e.code()}, {e.details()}')
                self.uc_close(self.connect_period)
                return
            signal_value = signal.value
            if elecont_pb2.ElecontSignalType.Value.Name(signal.type.value) == 'VISIBLE_STRING_255':
                signal_value = signal_value.replace('\x00', '')
            if signal_value != value or signal.quality != 0:
                # if signal.value != value: print(f'{userdata} value {signal.value.replace('\x00', '')}: {new_value}')
                signal.value = str(value)
                signal.quality = 0
                signal.time = self.get_time_stamp()
                if self.trace: print(f'{datetime.now().time()} Set {userdata}: {value}')
                try:
                    self.uc_stub.SetSignal(signal)
                except grpc.RpcError as e:
                    print(f'{datetime.now().time()} UserChannel. SetSignal error: {e.code()}, {e.details()}')
                    self.uc_close(self.connect_period)
                    return

    # метод вычисляет номер ключа
    def get_key_number(self, lic_str):
        if lic_str != 'None':
            lic_num = int(lic_str.split(';')[1])
            if lic_num < 0:
                lic_num = 0x100000000 + lic_num
            lic_str = format(lic_num, 'x')
        return lic_str
        
    # метод запрашивает состояние сервиса
    def get_state(self):
        try:
            self.uc_stub.GetState(elecont_pb2.Empty())           
        except grpc.RpcError as e:
            print(f'{datetime.now().time()} UserChannel. GetState error: {e.code()}, {e.details()}')
            self.uc_close(self.connect_period)                
                
    # метод возвращает текущее время в формате КС
    def get_time_stamp(self):
        tz = timezone(timedelta(hours=self.time_delta))
        time_now = datetime.now().replace(tzinfo = tz)
        timestamp = round(time_now.timestamp() * 1000)
        return timestamp
        
    # метод закрывает соединение UserChannel
    def uc_close(self, tSleep = 0):
        print(f'{datetime.now().time()} UserChannel. Close connect...')
        self.uc_connect_status = False
        try:
            self.uc_channel.close()
        except:
            pass
        time.sleep(tSleep)    # метод закрывает соединение UserChannel

    # метод закрывает соединение ApiGateWay        
    def gw_close(self, tSleep = 0):
        print(f'{datetime.now().time()} ApiGateWay. Close connect...')
        self.gw_connect_status = False
        try:
            self.gw_channel.close()
        except:
            pass
        time.sleep(tSleep)
    
    # финализатор класса
    def __del__(self):
        print(f'{datetime.now().time()} Close all connections (finally)...')
        try:
            self.uc_close.close()
            self.gw_close.close()
        except:
            pass