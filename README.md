本项目编程完全采用AI进行。<br>
当前有两个功能组，页面1为PDPS项目管理，页面2为针对Catia的焊点处理

<p>页面1:</p>
<img width="2302" height="92" alt="image" src="https://github.com/user-attachments/assets/6476bbb1-efa4-4176-93d1-ad4d804962d8" />
本项目主要针对单机版的PDPS，根据PSZ文件进行项目管理。<br>
1、添加项目目录，软件会自己检索后缀为psz的文件<br>
2、项目路径格式为：公司-工厂-区域-项目名称（项目代号）-Library<br>
3、自动检索psz目录下的Library文件夹，并将其设置为system Root，无Library时需要自行修改指定，修改后需要点选更新按钮<br>
4、主要针对打开psz文件需要频繁设置system Root的问题。

<p>页面2：</p>
<img width="2295" height="1395" alt="image" src="https://github.com/user-attachments/assets/2cb81789-bb29-4df7-9f20-46a9472f418a" />

功能1：提取part里面的点特征，导出点坐标，点坐标生成点球等<br>
功能2：针对焊点为实体球的情况，扫描part的多个几何体，提前几何体的重心参数，根据重心生成点，可直接导出，或利用功能1中的提取点特征，将其导出为PDPS可用的mfg.csv文件。 
