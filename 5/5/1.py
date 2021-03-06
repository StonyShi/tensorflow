# coding=utf-8
import tensorflow as tf
from mido import MetaMessage, Message, MidiFile, MidiTrack
import numpy as np
import os

# 跨度，最低值，最高值
lower_bound = 24
upper_bound = 102
span = upper_bound - lower_bound

curr_dir = os.path.dirname(__file__)
model_dir = os.path.join(curr_dir, "model")
if not os.path.exists(model_dir):
    os.mkdir(model_dir)

# midi文件转Note(音符)
def midiToNoteStateMatrix(midi_file_path, squash=True, span=span):
    mid = MidiFile(midi_file_path)

    # 多少秒
    time_left = []
    for track in mid.tracks:
        time_left.append(track[0].time)

    print("time_left",time_left)
    # 所有的音轨
    posns = [0 for track in mid.tracks]
    
    # 状态列表
    statematrix = []
    time = 0

    # 先插入空白的
    state = [[0,0] for x in range(span)]
    statematrix.append(state)

    condition = True
    while condition:
        # mid.ticks_per_beat = 120 含义： 1 tick 的时间长度为 1/120 拍
        # 一小节结束时，最后一个音符重复插入
        if time % (mid.ticks_per_beat / 4) == (mid.ticks_per_beat / 8):
            oldstate = state
            state = [[oldstate[x][0],0] for x in range(span)]
            statematrix.append(state)
        # 按音轨循环    
        for i in range(len(time_left)):
            # 如果midi终止了，中断循环
            if not condition:
                break
            # time==0 表示 1/4 音符，如果是 1/4 音符继续
            while time_left[i] == 0:
                track = mid.tracks[i]

                # 音符的位置，从 0 开始
                pos = posns[i]
 
                msg = track[pos]  
                if not msg.is_meta:               
                    if msg.type=='note_on':
                        if (msg.note < lower_bound) or (msg.note >= upper_bound):
                            pass  # 这里直接抛弃实际上有问题的，因为丢失了音符
                        else:
                            # 音符的强度 这里的处理也损失了信息
                            if msg.velocity == 0:
                                state[msg.note-lower_bound] = [0, 0]
                            else:       
                                state[msg.note-lower_bound] = [1, 1]
                elif msg.type == 'time_signature':
                    if msg.numerator not in (2, 4):
                        out =  statematrix
                        condition = False
                        break
                
                # 如果当前音轨的时长和下一个音符一致，继续循环读取下一个音符
                # 如果下一个音符没有了，标记为 None，   
                try:
                    time_left[i] = track[pos + 1].time
                    posns[i] += 1
                except IndexError:
                    time_left[i] = None

            # 如果当前不为空，减1
            if time_left[i] is not None:
                time_left[i] -= 1
 
        if all(t is None for t in time_left):
            break
 
        time += 1
 
    S = np.array(statematrix)
    statematrix = np.hstack((S[:, :, 0], S[:, :, 1]))
    statematrix = np.asarray(statematrix).tolist()
    return statematrix
 
# Note转midi文件
def noteStateMatrixToMidi(statematrix, filename="output_file.mid", span=span):
    statematrix = np.array(statematrix)
    if not len(statematrix.shape) == 3:
        statematrix = np.dstack((statematrix[:, :span], statematrix[:, span:]))
    statematrix = np.asarray(statematrix)

    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)

    span = upper_bound-lower_bound
    tickscale = 55
    
    lastcmdtime = 0
    prevstate = [[0,0] for x in range(span)]
    for time, state in enumerate(statematrix + [prevstate[:]]):  
        offNotes = []
        onNotes = []
        for i in range(span):
            n = state[i]
            p = prevstate[i]
            if p[0] == 1:
                if n[0] == 0:
                    offNotes.append(i)
                elif n[1] == 1:
                    offNotes.append(i)
                    onNotes.append(i)
            elif n[0] == 1:
                onNotes.append(i)
        for note in offNotes:
            track.append(Message('note_off', note=note+lower_bound, velocity=0, time=(time-lastcmdtime)*tickscale))            
            lastcmdtime = time
        for note in onNotes:
            track.append(Message('note_on', note=note+lower_bound, velocity=40, time=(time-lastcmdtime)*tickscale))            
            lastcmdtime = time
            
        prevstate = state
    
    track.append(MetaMessage('end_of_track', time=0))

    mid.save(os.path.join(curr_dir,filename))
 
# 读取midi数据
def get_songs(midi_path):
    files = os.listdir(midi_path)
    songs = []
    for f in files:
        if f[0]=='.': continue
        f = midi_path+'/'+f
        print('加载:', f)
        try:
            song = np.array(midiToNoteStateMatrix(f))
            if np.array(song).shape[0] > 64:
                songs.append(song)
                print(song)
                return songs
        except Exception as e:
            print('数据无效: ', e)
    print("读取的有效midi文件个数: ", len(songs))
    return songs
 
# midi目录中包含了下载的midi文件
midi_dir=os.path.join(curr_dir,'midi')
songs = get_songs(midi_dir)
note_range = upper_bound - lower_bound
# 音乐长度
n_timesteps = 128
n_input = 2 * note_range * n_timesteps
n_hidden = 64
 
X = tf.placeholder(tf.float32, [None, n_input])
W = None
bh = None
bv = None
 
# 一个简单的小范围内随机取整 0，1
def sample(probs):
    return tf.floor(probs + tf.random_uniform(tf.shape(probs), 0, 1))

# ?  
def gibbs_sample(k):
    def body(count, k, xk):
        hk = sample(tf.sigmoid(tf.matmul(xk, W) + bh))
        xk = sample(tf.sigmoid(tf.matmul(hk, tf.transpose(W)) + bv))
        return count+1, k, xk
 
    count = tf.constant(0)
    def condition(count,  k, xk):
        return count < k
    [_, _, x_sample] = tf.while_loop(condition, body, [count, tf.constant(k), X])
 
    x_sample = tf.stop_gradient(x_sample) 
    return x_sample
 
#定义神经网络 同时兼顾 音符和音强
def neural_network():
    global W
    W  = tf.Variable(tf.random_normal([n_input, n_hidden], 0.01))
    global bh
    bh = tf.Variable(tf.zeros([1, n_hidden],  tf.float32))
    global bv
    bv = tf.Variable(tf.zeros([1, n_input],  tf.float32))
 
    x_sample = gibbs_sample(1)
    h = sample(tf.sigmoid(tf.matmul(X, W) + bh))
    h_sample = sample(tf.sigmoid(tf.matmul(x_sample, W) + bh))
 
    learning_rate = tf.constant(0.005, tf.float32)
    size_bt = tf.cast(tf.shape(X)[0], tf.float32)
    W_adder  = tf.multiply(learning_rate/size_bt, tf.subtract(tf.matmul(tf.transpose(X), h), tf.matmul(tf.transpose(x_sample), h_sample)))
    bv_adder = tf.multiply(learning_rate/size_bt, tf.reduce_sum(tf.subtract(X, x_sample), 0, True))
    bh_adder = tf.multiply(learning_rate/size_bt, tf.reduce_sum(tf.subtract(h, h_sample), 0, True))
    update = [W.assign_add(W_adder), bv.assign_add(bv_adder), bh.assign_add(bh_adder)]
    return update
 
# 训练神经网络
def train_neural_network():
    update = neural_network()
 
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
 
        saver = tf.train.Saver(tf.all_variables())
 
        epochs = 10000
        batch_size = 64
        for epoch in range(epochs):
            for song in songs:
                song = np.array(song)
                song = song[:int(np.floor(song.shape[0]/n_timesteps) * n_timesteps)]
                song = np.reshape(song, [song.shape[0]//n_timesteps, song.shape[1] * n_timesteps])
        
                for i in range(1, len(song), batch_size): 
                    train_x = song[i:i+batch_size]
                    sess.run(update, feed_dict={X: train_x})
            print(epoch)
            # 保存模型
            if epoch == epochs - 1:
                saver.save(sess, os.path.join(model_dir,'midi.module'))
 
        # 生成midi
        sample = gibbs_sample(1).eval(session=sess, feed_dict={X: np.zeros((1, n_input))})
        S = np.reshape(sample[0,:], (n_timesteps, 2 * note_range))
        print(S)
        noteStateMatrixToMidi(S, os.path.join(curr_dir,"auto_gen_music.mid"))
        print('生成 auto_gen_music.mid 文件')
 
train_neural_network()