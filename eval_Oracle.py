
import queue
import threading
import time
import argparse
import time
from sys import platform
import torch

from models import *  # set ONNX_EXPORT in models.py
from utils.datasets import *
from utils.utils import *
import argparse
import time
from sys import platform

from models import *  # set ONNX_EXPORT in models.py
from utils.datasets import *
from utils.utils import *
from torch.autograd import Variable

import torch.optim as optim
from loss_preparation import TKD_loss
import torch.distributed as dist
import os
import scipy.io as sio



import threading

global exitFlag
exitFlag=[False]

import os


from classes import *


def Argos(opt):

   img_size = (320, 192) if ONNX_EXPORT else opt.img_size  # (320, 192) or (416, 256) or (608, 352) for (height, width)

   device = torch_utils.select_device(force_cpu=ONNX_EXPORT)




   ################ STUDENT ##########################

   s_weights, half = opt.s_weights, opt.half

   # Initialize model
   s_model = Darknet(opt.s_cfg, img_size)

   s_model.feture_index=[8,12]
   # Load weights
   if s_weights.endswith('.pt'):  # pytorch format
       s_model.load_state_dict(torch.load(s_weights, map_location=device)['model'])
   else:  # darknet format
       _ = load_darknet_weights(s_model, s_weights)


   # Eval mode
   s_model.to(device).eval()
   model=s_model
   # Half precision
   half = half and device.type != 'cpu'  # half precision only supported on CUDA
   if half:
       s_model.half()

   TKD_decoder = Darknet('cfg/TKD_decoder.cfg', img_size)


   #if s_weights.endswith('.pt'):  # pytorch format
   TKD_decoder.load_state_dict(torch.load('weights/TKD.pt', map_location=device)['model'])

   ################ Teacher ##########################

   o_weights, half = opt.o_weights, opt.half
   # Initialize model
   o_model = Darknet(opt.o_cfg, img_size)

   # Load weights
   if o_weights.endswith('.pt'):  # pytorch format
       o_model.load_state_dict(torch.load(o_weights, map_location=device)['model'])
   else:  # darknet format
       _ = load_darknet_weights(o_model, o_weights)

   # Eval mode
   o_model.to(device).eval()

   # Half precision
   half = half and device.type != 'cpu'  # half precision only supported on CUDA
   if half:
       o_model.half()

   ################## Oracle for inference ###################

   Oracle_model = Darknet(opt.o_cfg, img_size)

   # Load weights
   if o_weights.endswith('.pt'):  # pytorch format
       Oracle_model.load_state_dict(torch.load(o_weights, map_location=device)['model'])
   else:  # darknet format
       _ = load_darknet_weights(Oracle_model, o_weights)

   # Eval mode
   Oracle_model.to(device).eval()

   # Half precision
   half = half and device.type != 'cpu'  # half precision only supported on CUDA
   if half:
       Oracle_model.half()

   threadList = opt.source

   threads = []
   threadID = 1
   students=[]



   info=student(threadID,TKD_decoder,o_model,opt.source,opt,dist,device)

   # Configure run

   nc = 9  # number of classes

   seen = 0
   model.eval()
   coco91class = coco80_to_coco91_class()
   s = ('%20s' + '%10s' * 6) % ('Class', 'Images', 'Targets', 'P', 'R', 'mAP', 'F1')
   p, r, f1, mp, mr, map, mf1 = 0., 0., 0., 0., 0., 0., 0.

   jdict, stats, ap, ap_class = [], [], [], []


   iou_thres = 0.5


   for source in info.source:

       webcam = source == '0' or source.startswith('rtsp') or source.startswith('http')
       streams = source == 'streams.txt'

       model.eval()

       info.TKD.eval().cuda()

       # Set Dataloader

       if streams:
           torch.backends.cudnn.benchmark = True  # set True to speed up constant image size inference
           dataset = LoadStreams(source, img_size=info.opt.img_size, half=info.opt.half)
       elif webcam:
           stream_img = True
           dataset = LoadWebcam(source, img_size=info.opt.img_size, half=info.opt.half)
       else:
           save_img = True
           dataset = LoadImages(source, img_size=info.opt.img_size, half=info.opt.half)


       # Run inference
       info.frame = torch.zeros([1, 3, info.opt.img_size, info.opt.img_size])
       oracle_T = Oracle()
       info.oracle.train().cuda()

       for path, img, im0s, vid_cap in dataset:

           info.collecting = True
           # Get detections



           info.frame[0, :, 0:img.shape[1], :] = torch.from_numpy(img)
           info.frame = info.frame.cuda()
           pred, _, feature = model(info.frame)
           info.TKD.img_size = info.frame.shape[-2:]
           pred_TKD, _ = info.TKD(feature)

           pred = torch.cat((pred, pred_TKD), 1)  # concat tkd and general decoder

           if not oracle_T.is_alive():

               oracle_T = Oracle()
               oracle_T.frame=info.frame
               oracle_T.feature=[Variable(feature[0].data, requires_grad=False),Variable(feature[1].data, requires_grad=False)]
               oracle_T.info=info
               oracle_T.start()

           # oracle_T.join()


           pred = non_max_suppression(pred, info.opt.conf_thres, info.opt.nms_thres)
           pred = pred[0]

           labels,_=Oracle_model(info.frame)

           labels = non_max_suppression(labels, 0.3, 0.5)
           labels=labels[0]



           if labels is not None:

               labels=labels[:,[6,0,1,2,3]].round()

               nl = len(labels)
           else:
               nl=None


           tcls = labels[:, 0].tolist() if nl else []  # target class
           seen+=1
           if pred is None:
               if nl:
                   stats.append(([], torch.Tensor(), torch.Tensor(), tcls))
               continue

           tcls = labels[:, 0].tolist() if nl else []  # target class
           correct = [0] * len(pred)

           if nl:
               detected = []
               tcls_tensor = labels[:, 0]

               # target boxes

               tbox = labels[:, 1:5]

               # Search for correct predictions
               for i, det in enumerate(pred):

                   pbox = det[0:4]

                   pcls = det[6]

                   # Break if all targets already located in image
                   if len(detected) == nl:
                       break

                   # Continue if predicted class not among image classes
                   if pcls.item() not in tcls:
                       continue

                   # Best iou, index between pred and targets

                   m = (pcls == tcls_tensor).nonzero().view(-1)
                   iou, bi = bbox_iou(pbox, tbox[m]).max(0)

                   # If iou > threshold and class is correct mark as correct
                   print(iou)
                   if iou > iou_thres and m[bi] not in detected:  # and pcls == tcls[bi]:
                       correct[i] = 1
                       detected.append(m[bi])
           print(correct)
           print('-------------')



           # Append statistics (correct, conf, pcls, tcls)
           # print(correct, pred[:, 4].cpu(), pred[:, 6].cpu(), tcls )
           stats.append((correct, pred[:, 4].cpu(), pred[:, 6].cpu(), tcls))
           stats1 = [np.concatenate(x, 0) for x in list(zip(*stats))]  # to numpy
           if len(stats1):
               p, r, ap, f1, ap_class = ap_per_class(*stats1)
               mp, mr, map, mf1 = p.mean(), r.mean(), ap.mean(), f1.mean()
           print(seen, mp, mr, map, mf1)

           '''

           b = str(gt[gt_counter][0][0]).split('0', 1)
           if int(b[1]) == int(image_index):
               pred=non_max_suppression(pred, info.opt.conf_thres, info.opt.nms_thres)
               pred = pred[0]
               if pred is not None:
                   pred[:, :4] = scale_coords(img.shape[1:], pred[:, :4], im0s.shape).round()

               seen += 1

               labels=[]

               for j in gt[gt_counter][1]:

                   labels.append([tcls_temp,j[0],j[1],j[2],j[3]])


               labels=torch.FloatTensor(labels).cuda()
               gt_counter += 1
               b = str(gt[gt_counter][0][0]).split('0', 1)
               nl = len(labels)

               if pred is None:

                   if nl:
                       stats.append(([], torch.Tensor(), torch.Tensor(), tcls))
                   continue

               tcls = labels[:, 0].tolist() if nl else []  # target class
               correct = [0] * len(pred)

               if nl:
                   detected = []
                   tcls_tensor = labels[:, 0]

                   # target boxes

                   tbox = labels[:, 1:5]

                   # Search for correct predictions
                   for i, det in  enumerate(pred):

                       pbox=det[0:4]

                       pcls=det[6]

                       # Break if all targets already located in image
                       if len(detected) == nl:

                           break

                       # Continue if predicted class not among image classes
                       if pcls.item() not in tcls:
                           continue

                       # Best iou, index between pred and targets

                       m = (pcls == tcls_tensor).nonzero().view(-1)
                       iou, bi = bbox_iou(pbox, tbox[m]).max(0)


                       # If iou > threshold and class is correct mark as correct
                       if iou > iou_thres and m[bi] not in detected:  # and pcls == tcls[bi]:
                           correct[i] = 1
                           detected.append(m[bi])
               # Append statistics (correct, conf, pcls, tcls)
               #print(correct, pred[:, 4].cpu(), pred[:, 6].cpu(), tcls )
               stats.append((correct, pred[:, 4].cpu(), pred[:, 6].cpu(), tcls))
               stats1 = [np.concatenate(x, 0) for x in list(zip(*stats))]  # to numpy
               if len(stats1):
                   p, r, ap, f1, ap_class = ap_per_class(*stats1)
                   mp, mr, map, mf1 = p.mean(), r.mean(), ap.mean(), f1.mean()
               print(seen, mp, mr, map, mf1)


           if (b[0]) != folder:
               folder = b[0]
               break



                       # Stream results
                       # if stream_img:

           info.results = []

           '''


       stats1 = [np.concatenate(x, 0) for x in list(zip(*stats))]  # to numpy
       if len(stats1):
           p, r, ap, f1, ap_class = ap_per_class(*stats1)
           mp, mr, map, mf1 = p.mean(), r.mean(), ap.mean(), f1.mean()
           #nt = np.bincount(stats[3].astype(np.int64), minlength=nc)  # number of targets per class
       else:
           nt = torch.zeros(1)

       # Print results
       pf = '%20s' + '%10.3g' * 6  # print format
       #print(pf % ('all', seen, nt.sum(), mp, mr, map, mf1))
       print( seen, mp, mr, map, mf1)




if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--s-cfg', type=str, default='cfg/yolov3-tiny.cfg', help='cfg file path')
    parser.add_argument('--o-cfg', type=str, default='cfg/yolov3.cfg', help='cfg file path')
    parser.add_argument('--data', type=str, default='data/coco.data', help='coco.data file path')
    parser.add_argument('--s-weights', type=str, default='weights/yolov3-tiny.weights', help='path to weights file')
    parser.add_argument('--o-weights', type=str, default='weights/yolov3.weights', help='path to weights file')
    parser.add_argument('--source', type=str, default=['/media/common/DATAPART1/datasets/UCF_Crimes/Videos/Training_Normal_Videos_Anomaly/Normal_Videos947_x264.mp4'], help='source')  # input file/folder, 0 for webcam
    parser.add_argument('--output', type=str, default='output', help='output folder')  # output folder
    parser.add_argument('--img-size', type=int, default=416, help='inference size (pixels)')
    parser.add_argument('--conf-thres', type=float, default=0.1, help='object confidence threshold')
    parser.add_argument('--nms-thres', type=float, default=0.3, help='iou threshold for non-maximum suppression')
    parser.add_argument('--fourcc', type=str, default='mp4v', help='output video codec (verify ffmpeg support)')
    parser.add_argument('--half', action='store_true', help='half precision FP16 inference')
    parser.add_argument("--backend", type=str, default='gloo',
                        help="Backend")
    parser.add_argument('-s', "--send", action='store_true',
                        help="Send tensor (if not specified, will receive tensor)")
    parser.add_argument("--master_addr", type=str,default='10.218.110.18',
                        help="IP address of master")
    parser.add_argument("--use_helper_threads", action='store_true',
                        help="Use multiple threads")
    parser.add_argument("--rank", type=int, default=1,
                        help="Rank of current worker")
    parser.add_argument('-p', "--master_port", default=12345,
                        help="Port used to communicate tensors")
    parser.add_argument("--intra_server_broadcast", action='store_true',
                        help="Broadcast within a server")

    opt = parser.parse_args()
    print(opt)

    with torch.no_grad():
        Argos(opt)