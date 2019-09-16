
import queue
import threading
import time
import argparse
import time
from sys import platform

from models import *  # set ONNX_EXPORT in models.py
from utils.datasets import *
from utils.utils import *
global exitFlag
exitFlag=[False]

from classes import *


def Argos(opt):

   img_size = (320, 192) if ONNX_EXPORT else opt.img_size  # (320, 192) or (416, 256) or (608, 352) for (height, width)

   device = torch_utils.select_device(force_cpu=ONNX_EXPORT)


   ################ STUDENT ##########################

   s_weights, half = opt.s_weights, opt.half

   # Initialize model
   s_model = Darknet(opt.s_cfg, img_size)

   # Load weights
   if s_weights.endswith('.pt'):  # pytorch format
       s_model.load_state_dict(torch.load(s_weights, map_location=device)['model'])
   else:  # darknet format
       _ = load_darknet_weights(s_model, s_weights)


   # Eval mode
   s_model.to(device).eval()

   # Half precision
   half = half and device.type != 'cpu'  # half precision only supported on CUDA
   if half:
       s_model.half()

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

   threadList = ["0","VIRAT_S_000003.mp4"]

   threads = []
   threadID = 1
   students=[]
   # Create new threads
   for tName in threadList:
      student_temp=student(threadID,tName,opt,device)
      students.append(student_temp)
      thread = F_loader(student_temp)
      thread.start()
      threads.append(thread)
      threadID += 1

   ################ Start Student ####################################
   threadID += 1
   S_detection=student_detection(s_model,students, threadID)
   S_detection.start()
   threads.append(S_detection)

   # Notify threads it's time to exit
   input()
   for student_temp in students:
       student_temp.exitFlag=True
   for index in range(len(students)):
       del students[0]
   # Wait for all threads to complete
   for t in threads:
      t.join()
   print ("Exiting Main Thread")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--s-cfg', type=str, default='cfg/yolov3-tiny.cfg', help='cfg file path')
    parser.add_argument('--o-cfg', type=str, default='cfg/yolov3-spp.cfg', help='cfg file path')
    parser.add_argument('--data', type=str, default='data/coco.data', help='coco.data file path')
    parser.add_argument('--s-weights', type=str, default='weights/yolov3-tiny.pt', help='path to weights file')
    parser.add_argument('--o-weights', type=str, default='weights/yolov3-spp.weights', help='path to weights file')
    parser.add_argument('--source', type=str, default='data/samples', help='source')  # input file/folder, 0 for webcam
    parser.add_argument('--output', type=str, default='output', help='output folder')  # output folder
    parser.add_argument('--img-size', type=int, default=416, help='inference size (pixels)')
    parser.add_argument('--conf-thres', type=float, default=0.3, help='object confidence threshold')
    parser.add_argument('--nms-thres', type=float, default=0.5, help='iou threshold for non-maximum suppression')
    parser.add_argument('--fourcc', type=str, default='mp4v', help='output video codec (verify ffmpeg support)')
    parser.add_argument('--half', action='store_true', help='half precision FP16 inference')
    opt = parser.parse_args()
    print(opt)

    with torch.no_grad():
        Argos(opt)