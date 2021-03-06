# coding=utf-8
#!/sbin/python
'''
一共需要运行二次，第一次为按64/32/16长度学习，再运行一次预测输出结果
本程序预测的分数是 ？
'''
import numpy as np
import paddle.v2 as paddle
from paddle.trainer_config_helpers import *
import json
import os
import random
import sys
import time
import shutil 
import logging
import pickle
import commands, re  
import threading
from collections import deque

home = "/home/kesci/work/"
data_path = "/mnt/BROAD-datasets/video/"
model_path = os.path.join(home,"model")
cls_param_file = os.path.join(model_path,"param_cls.tar")
status_file = os.path.join(model_path,"status5.json")

result_json_file = os.path.join(model_path,"ai.json")
out_dir = os.path.join(model_path, "out")
if not os.path.exists(model_path): os.mkdir(model_path)
if not os.path.exists(out_dir): os.mkdir(out_dir)
np.set_printoptions(threshold=np.inf)

training_path = os.path.join(data_path,"training","image_resnet50_feature")
validation_path = os.path.join(data_path,"validation","image_resnet50_feature")
testing_path = os.path.join(data_path,"testing","image_resnet50_feature")

# 最长训练时间
max_train_time = 3600*3
# 每一轮最长训练时间
max_epoch_time = 3600//2
class_dim=4       # 分类 0，空白  1 过程，2，开始/结束， 
mark_length=8     # 标记开始和结束长度
learning_rate = 1e-4 # 学习速率
batch_size=8     # 每次最大学习多少批
is_train = True    # 是训练还是预测

status ={}
status["starttime"]=time.time()  #训练开始时间
status["steptime"]=time.time()   #训练每一步时间
status["usedtime"]=0             #训练总共花费时间  
status["epoch_usedtime"]=0             #训练总共花费时间  
status["cost"]=0                 #训练总cost
status["train_size"]=0   # 学习的关键帧长度
status["epoch_count"]=0   #当前学习轮次，一共轮
status["buffer_size"]=1000

if os.path.exists(status_file):
    status = json.load(open(status_file,'r'))
    print(status)
    if status["usedtime"] > max_train_time:
        is_train = False
        print("train time is over, start infer")

def set_train_size():
    if status["epoch_count"]==0:
        status["train_size"]=160
        status["buffer_size"]=1000
    elif status["epoch_count"]==1:
        status["train_size"]=160
        status["buffer_size"]=1000
    else:
        status["train_size"]=160
        status["buffer_size"]=1000
            
buffers = {"0":None,"1":None,"2":None}

def setBuffer():
    for buff in buffers:
        if buffers[buff]!=None:
            buffers[buff].clear()
    print("buffer size:",status["buffer_size"])
    buffers["0"] = deque(maxlen=int(status["buffer_size"]))
    buffers["1"] = deque(maxlen=int(status["buffer_size"]))
    buffers["2"] = deque(maxlen=int(status["buffer_size"]))

def load_data(filter=None):
    data = json.loads(open(os.path.join(data_path,"meta.json")).read())
    training_data = []
    validation_data = []
    testing_data = []
    training_lengths=[]    
    for data_id in data['database']:
        if filter!=None and data['database'][data_id]['subset']!=filter:
            continue
        if data['database'][data_id]['subset'] == 'training':
            if os.path.exists(os.path.join(training_path, "%s.pkl"%data_id)):
                training_data.append({'id':data_id,'data':data['database'][data_id]['annotations']})
                for annotations in data['database'][data_id]['annotations']:
                    training_lengths.append(annotations['segment'][1]-annotations['segment'][0])
        elif data['database'][data_id]['subset'] == 'validation':
            if os.path.exists(os.path.join(validation_path, "%s.pkl"%data_id)):
                validation_data.append({'id':data_id,'data':data['database'][data_id]['annotations']})
        elif data['database'][data_id]['subset'] == 'testing':
            if os.path.exists(os.path.join(testing_path, "%s.pkl"%data_id)):
                testing_data.append({'id':data_id,'data':data['database'][data_id]['annotations']})
    print('load data train %s, valid %s, test %s'%(len(training_data), len(validation_data), len(testing_data)))
    _max = int(max(training_lengths))
    _min = int(min(training_lengths))
    _avg = int(sum(training_lengths)/len(training_lengths))
    print("annotations: max %d, min %d, avg %d"%(_max, _min, _avg))
    return training_data, validation_data, testing_data

training_data, validation_data, testing_data = load_data()

def printLayer(layer):
    print("depth:",layer.depth,"height:",layer.height,"width:",layer.width,"num_filters:",layer.num_filters,"size:",layer.size,"outputs:",layer.outputs)

def cnn(input,filter_size,num_channels,num_filters=64, stride=2, padding=1):
    return paddle.layer.img_conv(input=input, filter_size=filter_size, num_channels=num_channels, 
        num_filters=num_filters, stride=stride, padding=padding, act=paddle.activation.Relu())

def pool(input, pool_size=2):
    return paddle.layer.img_pool(input=input, pool_size=pool_size, pool_size_y=pool_size, 
                                 stride=2, padding=0, padding_y=0, pool_type=paddle.pooling.Avg())  
def conv_bn_layer(input, ch_out, filter_size, stride, padding, active_type=paddle.activation.Relu(), ch_in=None):
    tmp = paddle.layer.img_conv(
        input=input,
        filter_size=filter_size,
        num_channels=ch_in,
        num_filters=ch_out,
        stride=stride,
        padding=padding,
        act=paddle.activation.Linear(),
        bias_attr=False)
    return paddle.layer.batch_norm(input=tmp, act=active_type)

def shortcut(ipt, n_in, n_out, stride):
    if n_in != n_out:
        return conv_bn_layer(ipt, n_out, 1, stride, 0, paddle.activation.Linear())
    else:
        return ipt

def basicblock(ipt, ch_out, stride):
    ch_in = ch_out * 2
    tmp = conv_bn_layer(ipt, ch_out, 3, stride, 1)
    tmp = conv_bn_layer(tmp, ch_out, 3, 1, 1, paddle.activation.Linear())
    short = shortcut(ipt, ch_in, ch_out, stride)
    return paddle.layer.addto(input=[tmp, short], act=paddle.activation.Relu())

def layer_warp(block_func, ipt, features, count, stride):
    tmp = block_func(ipt, features, stride)
    for i in range(1, count):
        tmp = block_func(tmp, features, 1)
    return tmp

def resnet(ipt, depth=32, input_size=1, pool=False):
    # depth should be one of 20, 32, 44, 56, 110, 1202
    assert (depth - 2) % 6 == 0
    n = (depth - 2) / 6
    nStages = {16, 64, 128}
    conv1 = conv_bn_layer(ipt, ch_in=input_size, ch_out=16, filter_size=3, stride=1, padding=1)
    res1 = layer_warp(basicblock, conv1, 16, n, 1)
    res2 = layer_warp(basicblock, res1, 32, n, 2)
    res3 = layer_warp(basicblock, res2, 64, n, 2)
    if pool:
         res3 = paddle.layer.img_pool(input=res3, pool_size=4, stride=1, pool_type=paddle.pooling.Avg())
    return res3

def normal_network(x, input_size=5):
    net = cnn(x,    5, input_size, 64, 2, 2) 
    net = pool(net)
    net = cnn(net,  5, 64, 128, 2, 2)
    net = cnn(net,  3, 128, 256, 2, 1)
    net = cnn(net,  1, 256, 512, 1, 0)
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    net = cnn(net,  1, 512, 1024, 1, 0)
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    return net

def normal_network_pad(x, input_size=5):
    net = cnn(x,    3, input_size, 64, 2 , 0) 
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    net = cnn(net,  3, 64, 128, 1, 0)
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    net = cnn(net,  3, 128, 256, 1, 0)
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    net = cnn(net,  3, 256, 512, 1, 0)
    net = paddle.layer.batch_norm(input=net, act=paddle.activation.Linear())
    return net

def lstm_network(input, fc_size, layer_size=6, drop=False):
    fc_para_attr = paddle.attr.Param(learning_rate=learning_rate)
    lstm_para_attr = paddle.attr.Param(initial_std=0., learning_rate=1.)
    para_attr = [fc_para_attr, lstm_para_attr]
    bias_attr = paddle.attr.Param(initial_std=0., l2_rate=0.)
    relu = paddle.activation.Relu()
    linear = paddle.activation.Linear()
    if drop:
        fc = paddle.layer.fc(input=input, size=fc_size, act=linear, bias_attr=bias_attr, 
                              layer_attr=paddle.attr.ExtraLayerAttribute(drop_rate=0.5))
    else:
        fc = paddle.layer.fc(input=input, size=fc_size, act=linear, bias_attr=bias_attr)
    lstm1 = paddle.layer.lstmemory(input=fc, act=relu, bias_attr=bias_attr)
    inputs = [fc, lstm1]
    for i in range(2, layer_size):
        if drop:
            fc = paddle.layer.fc(input=inputs, size=fc_size, act=linear, bias_attr=bias_attr, 
                                  layer_attr=paddle.attr.ExtraLayerAttribute(drop_rate=0.1))
        else:
            fc = paddle.layer.fc(input=inputs, size=fc_size, act=linear, bias_attr=bias_attr)
        lstm = paddle.layer.lstmemory(input=fc, reverse=(i % 2) == 0, act=relu, bias_attr=bias_attr)
        inputs = [fc, lstm]  
#     fc_last = paddle.layer.pooling(input=inputs[0], pooling_type=paddle.pooling.Max(), agg_level=AggregateLevel.TO_SEQUENCE)
#     lstm_last = paddle.layer.pooling(input=inputs[1], pooling_type=paddle.pooling.Max(), agg_level=AggregateLevel.TO_SEQUENCE)
#     inputs = paddle.layer.concat([fc_last, lstm_last]) 
    inputs = paddle.layer.concat(inputs)
    return inputs

def network():
    x = paddle.layer.data(name='x', height=16, width=16, type=paddle.data_type.dense_vector_sequence(2048))   

    a = paddle.layer.data(name='a', type=paddle.data_type.integer_value_sequence(class_dim))

#     net = resnet(x, 20, 8)
    net = normal_network_pad(x, 8)
    net = lstm_network(net, 1024, 6, False)
       
    net = paddle.layer.fc(input=net, size=1024, act=paddle.activation.Linear())
    net_class_fc = paddle.layer.fc(input=net, size=class_dim, act=paddle.activation.Softmax())
    cost_class = paddle.layer.classification_cost(input=net_class_fc, label=a)

    adam_optimizer = paddle.optimizer.Adam(
        learning_rate=learning_rate,
        learning_rate_schedule="pass_manual",
        learning_rate_args="1:1.,2:0.1,3:0.05,4:0.01,5:0.005,6:0.001",
        regularization=paddle.optimizer.L2Regularization(rate=8e-4),
        model_average=paddle.optimizer.ModelAverage(average_window=0.5))
    return cost_class, adam_optimizer, net_class_fc

def add_data_to_list(label, v_data): 
    count = 2
    for j in range(count):  
        
        w = v_data.shape[0]
        need_remove_index=[]
        for i in range(0,w):
            if (label[i]==0 or label[i]==1) and random.random()>0.8: 
                need_remove_index.append(i)
        _labels= np.delete(label, need_remove_index)
        _v_datas= np.delete(v_data, need_remove_index, axis=0)

        start = random.randint(0,status["train_size"])        
        w = _v_datas.shape[0]
        _data=[]
        _label=[]

        if j%2==0:
            for i in range(start, w):
                _data.append(_v_datas[i])
                _label.append(int(_labels[i]))
                if len(_data)==status["train_size"]:
                    yield _data, _label
                    _data = []
                    _label = []
        else:
            for i in range(w-start-1, 0, -1):
                _data.append(_v_datas[i])
                _label.append(int(_labels[i]))
                if len(_data)==status["train_size"]:
                    yield _data, _label
                    _data = []
                    _label = []
            

def pre_data():
    size = len(training_data)
    datas=[]
    labels=[]
    buffer_size = status["buffer_size"]

    while True:
        t_data = random.choice(training_data)
        v_data = np.load(os.path.join(training_path, "%s.pkl"%t_data["id"]))  
        w = v_data.shape[0]
        label = np.zeros(w, dtype=np.int8)
        for annotations in t_data["data"]:
            segment = annotations['segment']
            start = int(round(segment[0]))
            end = int(round(segment[1]))
            for i in range(start, end+1):
                if i<0 or i>=w: continue                  
                if i>=start and i<=start+mark_length: 
                    label[i] = 2
                elif i>=end-mark_length and i<=end:
                    label[i] = 3
                elif label[i] == 0:
                    label[i] = 1
        
        for _data, _label in add_data_to_list(label, v_data): 
            _rate = 1.0*sum(_label)/len(_label)
            if _rate<=0.2:
                data = buffers["0"]
            elif _rate>=0.8:
                data = buffers["1"]
            else:
                data = buffers["2"]
 
            if len(buffers["2"]) < buffer_size//2:
                data = buffers["2"]
            data.append((t_data["id"], _data, _label))
      
        # 每一轮只学习1小时
        if status["epoch_usedtime"] > max_epoch_time:
            status["epoch_usedtime"] = 0
            status["epoch_count"] += 1
            break
            
#         print("readed %s/%s %s.pkl, size: %s/%s"%(c,size,t_data["id"],len(data_0[0]),len(data_1[0])))

def reader_get_image_and_label():
    def reader():
        set_train_size()
        setBuffer()
        t1 = threading.Thread(target=pre_data, args=())
        t1.start()
        
        while True:
            full=True
            for buffer_name in buffers:
                if len(buffers[buffer_name])<status["buffer_size"]//2:
                    print("cacheing", len(buffers["0"]), len(buffers["1"]), len(buffers["2"]))
                    time.sleep(5)
                    full=False
                    break
            if full: break               
            
        status["steptime"]=time.time()    
        while t1.isAlive(): 
            k=random.random()
            if k<0.2:
                data = buffers["0"]
            elif k>0.8:
                data = buffers["1"]
            else:
                data = buffers["2"]      
            _data_id, _data, _lable = random.choice(data)
            yield _data, _lable
    return reader

def event_handler(event):
    if isinstance(event, paddle.event.EndIteration):
        #记下每次训练的打分
        if status["cost"]==0:
            status["cost"] = event.cost
        else:
            status["cost"] = status["cost"]*0.99 + event.cost*0.01
        
        if event.batch_id>0 and event.batch_id % (8192*10//(batch_size*status["train_size"])) == 0:
            print "RT %.2f,ST %.2f, Pass %d, Batch %d, Cost %.2f/%.2f, %s" % (
                max_train_time-status["usedtime"], time.time() - status["steptime"], event.pass_id,
                event.batch_id, event.cost, status["cost"], event.metrics) 
            status["usedtime"] += time.time() - status["steptime"]
            status["epoch_usedtime"] += time.time() - status["steptime"]
            status["steptime"] = time.time()

        if event.batch_id>0 and event.batch_id % 500 == 0:
            print("data_0: %d data_1: %d data_2: %d"%(len(buffers["0"]),len(buffers["1"]),len(buffers["2"])))
            cls_parameters.to_tar(open(cls_param_file, 'wb'))
            json.dump(status, open(status_file,'w'))
                             
def train():
    print('set reader ...')
    train_reader = paddle.batch(reader_get_image_and_label(), batch_size=batch_size)
    feeding_class={'x':0, 'a':1} 
    trainer = paddle.trainer.SGD(cost=cost, parameters=cls_parameters, update_equation=adam_optimizer)
    print("start train class ...")
    trainer.train(reader=train_reader, event_handler=event_handler, feeding=feeding_class, 
                  num_passes=max_train_time//max_epoch_time)
    print("paid:", time.time() - status["starttime"])
    cls_parameters.to_tar(open(cls_param_file, 'wb'))
    json.dump(status, open(status_file,'w'))

def infer():
#     status["train_size"]=32
    inferer = paddle.inference.Inference(output_layer=net_class_fc, parameters=cls_parameters)
    save_file = os.path.join(out_dir,"test_%s.pkl"%class_dim)
    infers={}
    for data in testing_data:
        filename = "%s.pkl"%data["id"]
        v_data = np.load(os.path.join(testing_path, filename))
        w = v_data.shape[0]
        values = np.zeros((w, class_dim))
        datas=[]
        print("start infered %s"%filename)
        for i in range(status["train_size"]):
            _values = []
            datas=[]
            for j in range(i, w):
                datas.append(v_data[j])
                if len(datas)==status["train_size"]:
                    probs = inferer.infer(input=[(datas,)])
                    for prob in probs:
                        _values.append(prob)
                    datas=[]
            if len(datas)>0:
                probs = inferer.infer(input=[(datas,)])
                for prob in probs:
                    _values.append(prob)
            for j, prob in enumerate(_values):
                values[i+j] = values[i+j]+prob
            sys.stdout.write('.')
            sys.stdout.flush()
            
        values2 = np.zeros((w,class_dim))    
        for i in range(status["train_size"]):
            _values = []
            datas=[]
            for j in range(w-i-1, 0, -1):
                datas.append(v_data[j])
                if len(datas)==status["train_size"]:
                    probs = inferer.infer(input=[(datas,)])
                    for prob in probs:
                        _values.append(prob)
                    datas=[]
            if len(datas)>0:
                probs = inferer.infer(input=[(datas,)])
                for prob in probs:
                    _values.append(prob)
                        
            for j, prob in enumerate(_values):
                values2[w-i-j-1] = values2[w-i-j-1]+prob
            sys.stdout.write('.')
            sys.stdout.flush()
        infers[data["id"]]=values+values2 
        print("infered %s"%filename)
    # print(infers)
    pickle.dump(infers,open(save_file,"wb"))
    
def infer_validation():
#     status["train_size"]=16
    inferer = paddle.inference.Inference(output_layer=net_class_fc, parameters=cls_parameters)
    infers={}
    save_file = os.path.join(out_dir,"validation_%s.pkl"%class_dim)
    for data in validation_data:
        if data["id"]!="310918400": continue
        filename = "%s.pkl"%data["id"]
        v_data = np.load(os.path.join(validation_path, filename))
        w = v_data.shape[0]
        values = np.zeros((w,class_dim))
        datas=[]  
        print("start infered %s"%filename)
        for i in range(status["train_size"]):
            _values = []
            datas=[]
            for j in range(i, w):
                datas.append(v_data[j])
                if len(datas)==status["train_size"]:
                    probs = inferer.infer(input=[(datas,)])
                    for prob in probs:
                        _values.append(prob)
                    datas=[]
            if len(datas)>0:
                probs = inferer.infer(input=[(datas,)])
                for prob in probs:
                    _values.append(prob)
            for j, prob in enumerate(_values):
                values[i+j] = values[i+j]+prob
            sys.stdout.write('.')
            sys.stdout.flush()
            
        values2 = np.zeros((w,class_dim))    
        for i in range(status["train_size"]):
            _values = []
            datas=[]
            for j in range(w-i-1, 0, -1):
                datas.append(v_data[j])
                if len(datas)==status["train_size"]:
                    probs = inferer.infer(input=[(datas,)])
                    for prob in probs:
                        _values.append(prob)
                    datas=[]
            if len(datas)>0:
                probs = inferer.infer(input=[(datas,)])
                for prob in probs:
                    _values.append(prob)
                        
            for j, prob in enumerate(_values):
                values2[w-i-j-1] = values2[w-i-j-1]+prob
            sys.stdout.write('.')
            sys.stdout.flush()
        infers[data["id"]]=values+values2 
        print("infered %s"%filename)
    pickle.dump(infers,open(save_file,"wb"))
    # print(infers)
    
if __name__ == '__main__':
    print("paddle init ...")
    paddle.init(use_gpu=True, trainer_count=1)
    cost, adam_optimizer, net_class_fc = network()

    if os.path.exists(cls_param_file):
        print("load %s, continue train ..."%cls_param_file)
        cls_parameters = paddle.parameters.Parameters.from_tar(open(cls_param_file,"rb"))
        if is_train:
            train()
        else:
            infer_validation()
#             infer()
    else:
        cls_parameters = paddle.parameters.create(cost)
        for name in cls_parameters.names():
            print(name, cls_parameters.get_shape(name))
        train()
    print("OK")