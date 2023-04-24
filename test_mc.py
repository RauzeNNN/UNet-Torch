import numpy as np
import cv2
import os
from tqdm import tqdm
import re
import torch
from Model import UNet
import torch.nn.functional as F

image_ext = ['.png', '.jpg', '.tif', '.tiff']


def natural_sort(l):
    def convert(text): return int(text) if text.isdigit() else text.lower()
    def alphanum_key(key): return [convert(c)
                                   for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)


def get_image_list(path):
    image_names = []
    for maindir, subdir, file_name_list in os.walk(path):
        for filename in file_name_list:
            if '_label' not in filename:
                apath = os.path.join(maindir, filename)
                ext = os.path.splitext(apath)[1]
                if ext in image_ext:
                    image_names.append(apath)
    return natural_sort(image_names)


def pre_process(img):
    img = np.float32(img)
    img = (img - img.mean()) / img.std()
    # HW to CHW (for gray scale)
    img = np.expand_dims(img, 0)
    img = np.expand_dims(img, 0)

    # HWC to CHW, BGR to RGB (for three channel)
    # img = img.transpose((2, 0, 1))[::-1]
    img = torch.as_tensor(img)
    return img


use_cuda = True
model_path = '/kuacc/users/ocaki13/hpc_run/workfolder/exp1_ultrasound/models/epoch83.pt'
model = UNet(1, 5, 64,
             use_cuda, True, 0.25)
model.load_state_dict(torch.load(model_path))
model.eval()
device = "cuda:0"
dtype = torch.cuda.FloatTensor
model.to(device=device)


val_path = '/kuacc/users/ocaki13/hpc_run/images/cizik'
image_list = get_image_list(val_path)

label_colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def create_rgb_mask(mask, label_colors):
    rgb_mask = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    rgb_mask[mask == 1] = label_colors[0]
    rgb_mask[mask == 2] = label_colors[1]
    rgb_mask[mask == 3] = label_colors[2]
    rgb_mask[mask == 4] = label_colors[3]

    return rgb_mask


save_dir = 'res'
if not os.path.exists(save_dir):
    os.mkdir(save_dir)


class Results:
    def __init__(self, save_dir, class_names):
        self.save_dir = save_dir
        if not os.path.exists(self.save_dir):
            os.mkdir(self.save_dir)
        self.image_save_dir = os.path.join(self.save_dir, 'images')
        if not os.path.exists(self.image_save_dir):
            os.mkdir(self.image_save_dir)
        self.tp_pw = 0
        self.tn_pw = 0
        self.fp_pw = 0
        self.fn_pw = 0
        self.class_names = class_names
        self.result_dict = {}
        for i in range(len(class_names)):
            self.result_dict[i] = {}
            self.result_dict[i]["iou"] = []
            self.result_dict[i]["dice"] = []
        self.background_iou = []
        self.background_dice = []
        self.seam_iou = []
        self.seam_dice = []

    def pixel_based_metrics(self, y_true, y_pred):
        """
        calculate metrics threating each pixel as a sample
        """
        # true positives
        tp_pp = np.sum((y_pred == 1) & (y_true == 1))
        # true negatives
        tn_pp = np.sum((y_pred == 0) & (y_true == 0))
        # false positives
        fp_pp = np.sum((y_pred == 1) & (y_true == 0))
        # false negatives
        fn_pp = np.sum((y_pred == 0) & (y_true == 1))

        return tp_pp, tn_pp, fp_pp, fn_pp

    def class_wise_metrics(self, y_true, y_pred):
        class_wise_iou = []
        class_wise_dice_score = []

        smoothening_factor = 0.00001
        for i in range(len(self.class_names)):

            intersection = np.sum((y_pred == i) * (y_true == i))
            y_true_area = np.sum((y_true == i))
            y_pred_area = np.sum((y_pred == i))
            combined_area = y_true_area + y_pred_area

            iou = (intersection + smoothening_factor) / \
                (combined_area - intersection + smoothening_factor)
            class_wise_iou.append(iou)

            dice_score = 2 * ((intersection) /
                              (combined_area + smoothening_factor))
            class_wise_dice_score.append(dice_score)

        return class_wise_iou, class_wise_dice_score

    def compare(self, gt_binary_mask, predicted_binary_mask):
        class_wise_iou, class_wise_dice_score = self.class_wise_metrics(
            gt_mask, p)

        for i in range(len(class_wise_iou)):
            self.result_dict[i]['iou'].append(class_wise_iou[i])
            self.result_dict[i]['dice'].append(class_wise_dice_score[i])

        tp_pp, tn_pp, fp_pp, fn_pp = self.pixel_based_metrics(
            gt_binary_mask, predicted_binary_mask)
        self.tp_pw += tp_pp
        self.tn_pw += tn_pp
        self.fp_pw += fp_pp
        self.fn_pw += fn_pp


for img_path in tqdm(image_list):
    image_name = img_path.split('/')[-1]
    image_name = image_name[:image_name.rfind('')]
    img_org = cv2.resize(cv2.imread(
        img_path, 0), (1200, 800))

    img = pre_process(img_org)
    outputs = model(img.to(device))
    probs = F.softmax(outputs, dim=1)
    print(probs.shape)
    print(probs.sum(1))

    probs = probs.data.cpu().numpy()
    # (shape: (batch_size, num_classes, img_h, img_w))
    outputs = outputs.data.cpu().numpy()
    # (shape: (batch_size, img_h, img_w))
    pred_label_imgs = np.argmax(outputs, axis=1)
    pred_label_imgs = pred_label_imgs.astype(np.uint8)
    print(np.unique(pred_label_imgs[0]))
    # read dist mask
    mask_path = img_path[:img_path.rfind('.')] + '_label.png'
    mask = cv2.resize(cv2.imread(
        mask_path, 0), (1200, 800))
    rgb_mask = create_rgb_mask(mask, label_colors)
    rgb_mask_pred = create_rgb_mask(pred_label_imgs[0], label_colors)

    score1 = probs[0, 1, :, :] * 255
    score2 = probs[0, 2, :, :] * 255
    score3 = probs[0, 3, :, :] * 255
    score4 = probs[0, 4, :, :] * 255

    score1_img = score1.astype(np.uint8)
    score2_img = score2.astype(np.uint8)
    score3_img = score3.astype(np.uint8)
    score4_img = score4.astype(np.uint8)

    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(2, 2)
    fig.set_figheight(12)
    fig.set_figwidth(12)
    axs[0, 0].imshow(score1_img, cmap='gray')
    axs[0, 0].title.set_text('class 1 (red)')
    axs[0, 1].imshow(score2_img, cmap='gray')
    axs[0, 1].title.set_text('class 2 (green)')
    axs[1, 0].imshow(score3_img, cmap='gray')
    axs[1, 0].title.set_text('class 3 (blue)')
    axs[1, 1].imshow(score4_img, cmap='gray')
    axs[1, 1].title.set_text('class 4 (yellow)')
    fig.savefig(os.path.join(save_dir, image_name+'_probdist.png'))

    seperater = np.zeros([img_org.shape[0], 15, 3], dtype=np.uint8)
    seperater.fill(155)

    # save_img_dist = np.hstack(
    #     [img_org, seperater, mask_dist, seperater, pred_dist])
    # cv2.imwrite(os.path.join(results_save_dir_images,
    #             image_name+'_dist.png'), save_img_dist)

    rgb_mask = cv2.cvtColor(rgb_mask, cv2.COLOR_BGR2RGB)
    rgb_mask_pred = cv2.cvtColor(rgb_mask_pred, cv2.COLOR_BGR2RGB)

    save_img_bin = np.hstack(
        [cv2.cvtColor(img_org, cv2.COLOR_GRAY2RGB), seperater, rgb_mask, seperater, rgb_mask_pred])
    cv2.imwrite(os.path.join(save_dir, image_name+'.png'), save_img_bin)
